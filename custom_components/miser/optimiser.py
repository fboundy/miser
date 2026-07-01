import logging
import asyncio
import pandas as pd
from numpy import arange
from datetime import datetime
from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import history
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    IMPORT_EXPORT,
    DATETIME_FORMAT_LONG,
    MODEL_DURATION_HOURS,
    MODEL_PERIOD_MINUTES,
    DEFAULTS,
    CONSUMPTION_SHAPE,
    SOLCAST_INTEGRATION,
    SOLCAST_PV_KEY,
    SOLCAST_DETAILED_FORECAST_ATTRIBUTE,
    SOLCAST_FORECAST_PERIODS,
    SOLCAST_COLUMNS,
    CONF_USE_CONSUMPTION_HISTORY,
    CONF_DAILY_CONSUMPTION_KWH,
    CONF_HISTORY_DAYS,
    CONF_LOAD_MARGIN,
    CONF_WEEKDAY_WEIGHTING,
    CONF_SOLCAST_CONFIDENCE,
    CONF_SHAPE_CONSUMPTION,
    CONF_OPTIMISE_DISCHARGING,
    COST_ENTITY_OBJECTS,
    MODEL_CONSUMPTION_TODAY,
    MODEL_BATTERY_SOC,
    MODEL_ENTITIES_AVAILABLE_WAIT,
    OPTIMISER_MAX_ITERS,
)
from .utils import get_value, get_entity_for_key, redact_sensitive

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")


def cl_to_weights(cl):
    wt90 = min(max(cl - 50, 0) / 40, 1)
    wt10 = min(max(50 - cl, 0) / 40, 1)
    wt50 = 1 - wt90 - wt10
    return wt50, wt10, wt90


async def optimise(hass: HomeAssistant, now=None):
    data = hass.data[DOMAIN]
    freq = pd.Timedelta(minutes=MODEL_PERIOD_MINUTES)
    # Access hass.data
    uuid = data["uuid"]
    model = data["model"]
    tz = model.tz

    str_log = f"optiMISER executed at: {datetime.now().strftime(DATETIME_FORMAT_LONG)}. UUID: {uuid}"
    _LOGGER.debug("*" * len(str_log))
    _LOGGER.debug(str_log)
    _LOGGER.debug("*" * len(str_log))

    start = pd.Timestamp.now(tz=tz).floor(freq)
    end = start.normalize() + pd.Timedelta(hours=MODEL_DURATION_HOURS)
    index = pd.date_range(start, end, freq=freq)
    _LOGGER.debug(f"Model Start: {start.strftime(DATETIME_FORMAT_LONG)}")
    _LOGGER.debug(f"Model End  : {end.strftime(DATETIME_FORMAT_LONG)}")

    await _get_consumption(hass=hass, start=start, end=end, freq=freq)
    await _get_solcast(hass=hass, start=start, end=end, freq=freq)
    await _get_prices(hass=hass, start=start, end=end, freq=freq)
    merged_model_data = pd.concat([getattr(model, x) for x in ["consumption", "solar", "prices"]], axis=1)
    _LOGGER.debug(f"Merged Model Data:\n{merged_model_data.to_string()}")
    # >>> Do some checks on the data validity here

    model.initial_soc = await get_value(hass, MODEL_BATTERY_SOC)
    retries = 0
    while model.initial_soc is None and retries < MODEL_ENTITIES_AVAILABLE_WAIT:
        await asyncio.sleep(1)
        model.initial_soc = await get_value(hass, MODEL_BATTERY_SOC)
        retries += 1

    if model.initial_soc is None:
        _LOGGER.warning("Unable to get Battery SOC - run failed")
        return
    else:
        model.set_start(pd.Timestamp.now(tz="UTC"))

    model.base_slots = []
    model.base_cost, model.base_flows = await _calculate_cost_and_flows(model, model.base_slots)
    _LOGGER.debug(f"Base cost: {model.base_cost:6.1f}")

    high_cost_swaps = await model.high_cost_swaps()
    model.swap_slots = high_cost_swaps
    model.swap_cost, model.swap_flows = await _calculate_cost_and_flows(model, model.swap_slots)
    _LOGGER.debug(f"Swap cost: {model.swap_cost:6.1f}")

    lcc_slots = await model.low_cost_charging(base_slots=model.swap_slots)
    model.lcc_slots = lcc_slots
    model.lcc_cost, model.lcc_flows = await _calculate_cost_and_flows(model, model.lcc_slots)
    _LOGGER.debug(f"LCC cost: {model.lcc_cost:6.1f}")

    lcc_best_cost = model.best_cost
    model.discharge_slots, model.discharge_cost, model.discharge_flows = await _optimise_discharge(
        model,
        base_slots=model.lcc_slots,
        base_cost=lcc_best_cost,
        fill_first=False,
    )
    _LOGGER.debug(f"Discharge cost: {model.discharge_cost:6.1f}")

    model.fill_first_slots, model.fill_first_cost, model.fill_first_flows = await _optimise_discharge(
        model,
        base_slots=model.lcc_slots,
        base_cost=lcc_best_cost,
        fill_first=True,
    )
    _LOGGER.debug(f"Fill-first cost: {model.fill_first_cost:6.1f}")

    optimise_discharging = await get_value(hass, CONF_OPTIMISE_DISCHARGING)
    cost_keys = ["base_cost", "swap_cost", "lcc_cost"]
    if optimise_discharging:
        cost_keys.extend(["discharge_cost", "fill_first_cost"])

    optimised_key = min(cost_keys, key=lambda key: getattr(model, key))
    model.optimised_cost = getattr(model, optimised_key)
    model.optimised_slots = list(getattr(model, optimised_key.replace("_cost", "_slots")))
    model.optimised_flows = getattr(model, optimised_key.replace("_cost", "_flows"))
    model.best_cost = model.optimised_cost
    _LOGGER.debug(f"Optimised cost: {model.optimised_cost:6.1f} ({optimised_key})")

    await _write_cost_entities(hass, model)


async def _write_cost_entities(hass: HomeAssistant, model) -> None:
    cost_entities = hass.data[DOMAIN].get(COST_ENTITY_OBJECTS, {})

    for key in ["base_cost", "swap_cost", "lcc_cost", "discharge_cost", "fill_first_cost", "optimised_cost"]:
        entity = cost_entities.get(key)
        if entity is not None:
            await entity.async_set_native_value(
                getattr(model, key, None),
                slots=_serialise_slots(getattr(model, key.replace("_cost", "_flows"), None)),
                flows=_serialise_flows(getattr(model, key.replace("_cost", "_flows"), None)),
            )


async def _optimise_discharge(model, base_slots: list, base_cost: float, fill_first: bool) -> tuple:
    best_slots = list(base_slots)
    best_cost = base_cost
    best_flows = await model.flows(slots=best_slots)
    iteration_base_slots = list(base_slots)
    label = "Fill-first discharge" if fill_first else "Discharge"

    model.best_cost = base_cost

    for iteration in range(OPTIMISER_MAX_ITERS):
        previous_best_cost = model.best_cost
        _LOGGER.debug(f"{label} iteration {iteration + 1}")

        discharge_base_slots = iteration_base_slots
        if fill_first:
            discharge_base_slots = await model.fill_first_charging(base_slots=iteration_base_slots)
            local_lcc_cost, _local_lcc_flows = await _calculate_cost_and_flows(model, discharge_base_slots)
            model.best_cost = local_lcc_cost
            _LOGGER.debug(f"{label} local fill cost: {local_lcc_cost:6.1f}")

        discharge_slots = await model.discharging(base_slots=discharge_base_slots)
        discharge_cost, discharge_flows = await _calculate_cost_and_flows(model, discharge_slots)
        _LOGGER.debug(f"{label} cost: {discharge_cost:6.1f}")

        if discharge_cost >= best_cost:
            model.best_cost = previous_best_cost
            _LOGGER.debug(f"No {label.lower()} improvement in iteration {iteration + 1}; stopping optimisation loop")
            break

        best_slots = discharge_slots
        best_cost = discharge_cost
        best_flows = discharge_flows
        iteration_base_slots = best_slots

    model.best_cost = best_cost
    return best_slots, best_cost, best_flows


async def _calculate_cost_and_flows(model, slots: list) -> tuple[float, pd.DataFrame]:
    net_cost = await model.net_cost(slots=slots)
    flows = await model.flows(slots=slots)
    return net_cost.sum(), flows


def _serialise_slots(flows: pd.DataFrame | None) -> list[dict]:
    if flows is None:
        return []

    serialised = []
    forced_flows = flows[flows["forced"] != 0]
    for start, row in forced_flows.iterrows():
        serialised.append(
            {
                "start": start.isoformat() if hasattr(start, "isoformat") else start,
                "power": _serialise_number(row.get("grid")),
            }
        )
    return serialised


def _serialise_flows(flows: pd.DataFrame | None) -> list[dict]:
    if flows is None:
        return []

    serialised = []
    for start, row in flows.iterrows():
        serialised.append(
            {
                "start": start.isoformat() if hasattr(start, "isoformat") else start,
                "grid": _serialise_number(row.get("grid")),
                "battery": _serialise_number(row.get("battery")),
                "load": _serialise_number(row.get("consumption")),
                "soc": _serialise_number(row.get("soc")),
                "soc_end": _serialise_number(row.get("soc_end")),
            }
        )
    return serialised


def _serialise_number(value):
    if pd.isna(value):
        return None
    return round(float(value), 1)


async def _get_consumption(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    """
    Get consumption and save it to hass.data[DOMAIN]['consumption']

    ** Requires EV logic **
    """
    index = pd.date_range(start=start, end=end, freq=freq, inclusive="left")
    # Set up a template dataframe with just index and time of day
    consumption = pd.DataFrame(index=index)
    consumption["time_of_day"] = consumption.index.time
    consumption["dow_tod"] = consumption.index.day_of_week + consumption.index.hour / 24

    use_consumption = await get_value(hass, CONF_USE_CONSUMPTION_HISTORY)
    if use_consumption:
        entity_id = get_entity_for_key(hass, MODEL_CONSUMPTION_TODAY)
        history_days = await get_value(hass, CONF_HISTORY_DAYS)
        load_margin = await get_value(hass, CONF_LOAD_MARGIN)
        weekday_weighting = await get_value(hass, CONF_WEEKDAY_WEIGHTING)
        if entity_id is not None:
            _LOGGER.debug(f"Loading {history_days} days consumption history from {entity_id}")
            consumption_history = await _get_hass_power_from_daily_kwh(hass, entity_id, history_days, freq=freq)

            if consumption_history is not None and not consumption_history.empty:
                # Add consumption margin
                consumption_history = consumption_history * (1 + load_margin / 100)

                # Group by time, take the mean and merge with the template
                consumption_by_time = consumption_history.groupby(consumption_history.index.time).mean().rename("mean")
                consumption = consumption.merge(consumption_by_time, "left", left_on="time_of_day", right_index=True)

                if history_days >= 7:
                    consumption_dow = consumption_history.set_axis(
                        consumption_history.index.day_of_week + consumption_history.index.hour / 24
                    )
                    consumption_dow = consumption_dow.groupby(consumption_dow.index).mean().rename("dow")
                    consumption = consumption.merge(consumption_dow, "left", left_on="dow_tod", right_index=True)
                    consumption["final"] = consumption["mean"] * (1 - weekday_weighting / 100) + consumption["dow"] * (
                        weekday_weighting / 100
                    )
                    # _LOGGER.debug(f"Consumption\n{consumption.to_string()}")

                else:
                    _LOGGER.debug(
                        f"  - Ignoring 'Day of Week Weighting' because only {history_days} days of history is available"
                    )
                    consumption["final"] = consumption["mean"]
            else:
                _LOGGER.warning(f"No usable consumption history available from {entity_id}; using configured fallback")

    if "final" not in consumption.columns:
        # Need to add config entities for manual case
        daily_consumption = await get_value(hass, CONF_DAILY_CONSUMPTION_KWH)
        if await get_value(hass, CONF_SHAPE_CONSUMPTION):
            daily = (
                pd.DataFrame(CONSUMPTION_SHAPE)
                .set_index("hours")
                .reindex(arange(0, 24.5, 0.5))
                .interpolate()
                .iloc[:-1]
            )
            daily["final"] = daily["consumption"] * daily_consumption / (daily["consumption"].sum() / 2000)
            daily.index = pd.to_datetime(daily.index, unit="h").time
            consumption = consumption.merge(daily, left_on="time_of_day", right_index=True)

        else:
            consumption["final"] = daily_consumption / 24

    hass.data[DOMAIN]["model"].consumption = consumption["final"].rename("consumption")

    return True


async def _get_solcast(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    config_entries = hass.config_entries.async_entries(SOLCAST_INTEGRATION)
    index = pd.date_range(start=start, end=end, freq=freq, inclusive="left")
    solcast = pd.DataFrame(index=index, data={"weighted": 0})

    # Log ConfigEntry contents
    if config_entries:
        # _LOGGER.debug("Solcast integration:")
        entry = config_entries[0]
        # log_config_entry(entry)

        entity_registry = er.async_get(hass=hass)
        solcast_entities = [
            entity for entity in entity_registry.entities.values() if entity.config_entry_id == entry.entry_id
        ]
        forecast_entities = [entity for entity in solcast_entities if SOLCAST_PV_KEY in entity.entity_id]
        forecast = []
        for entity in forecast_entities:
            forecast_period = entity.entity_id.split(SOLCAST_PV_KEY)[1][1:]
            if forecast_period in SOLCAST_FORECAST_PERIODS:
                state = hass.states.get(entity.entity_id)
                if state is not None:
                    forecast += state.attributes.get(SOLCAST_DETAILED_FORECAST_ATTRIBUTE, [])

        if forecast:
            solcast = pd.DataFrame(forecast).set_index("period_start").sort_index().loc[start : end - freq]
            confidence_level = await get_value(hass, CONF_SOLCAST_CONFIDENCE)
            weights = cl_to_weights(confidence_level)
            solcast["weighted"] = 0
            for weight, col in zip(weights, SOLCAST_COLUMNS):
                solcast["weighted"] += weight * solcast[col] * 1000
            # _LOGGER.debug(f"\n{solcast.to_string()}")
        else:
            _LOGGER.warning("No Solcast forecast data available; using zero solar forecast")
    else:
        _LOGGER.warning("Solcast integration is not configured; using zero solar forecast")
    hass.data[DOMAIN]["model"].solar = solcast["weighted"].rename("solar")


async def _get_prices(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    _LOGGER.debug(redact_sensitive(hass.data[DOMAIN]["octopus_info"]))
    price = {}
    for direction in IMPORT_EXPORT:
        tariff = hass.data[DOMAIN]["tariffs"].get(direction, None)
        if tariff is not None:
            price[direction] = await tariff.to_df(start=start, end=end - freq)
            price[direction].rename(columns={"unit": direction}, inplace=True)
            # _LOGGER.debug(f"\n{price[direction]}")
    if "import" not in price:
        raise ValueError("Import tariff prices are required")
    prices = pd.concat(price.values(), axis=1)
    if "export" not in prices.columns:
        prices["export"] = 0
    hass.data[DOMAIN]["model"].prices = prices


async def _get_hass_power_from_daily_kwh(
    hass, entity_id, days=DEFAULTS[CONF_HISTORY_DAYS], freq=pd.Timedelta(minutes=MODEL_PERIOD_MINUTES)
):
    df = await _hass_to_df(hass, entity_id, days=days)
    if df is not None and not df.empty:
        x = df.diff().clip(0).fillna(0).cumsum() + df.iloc[0]
        x.index = x.index.round("1s")
        x = x[~x.index.duplicated()]
        y = -pd.concat([x.resample("1s").interpolate().resample(freq).asfreq(), x.iloc[-1:]]).diff(-1)
        dt = y.index.diff().total_seconds() / pd.Timedelta("60min").total_seconds() / 1000
        df = y[1:-1] / dt[2:]
    else:
        df = None
    return df


async def _hass_to_df(
    hass: HomeAssistant,
    entity_id: str,
    end_time: datetime = None,
    days: int = 1,
    start_time: datetime = None,
) -> list:
    """Fetch the state history for an entity."""
    # Specify a time range (optional)
    end_time = end_time or pd.Timestamp.now(tz="UTC")
    start_time = start_time or end_time - pd.Timedelta(hours=days * 24)

    # Get history data
    states = await hass.async_add_executor_job(
        history.get_significant_states,
        hass,
        start_time,
        end_time,
        [entity_id],
    )

    states = states.get(entity_id, [])

    # Return state history for the specified entity
    df = pd.Series(index=[state.last_updated for state in states], data=[state.state for state in states])
    df.index = pd.to_datetime(df.index)
    df = pd.to_numeric(df, errors="coerce").dropna()
    return df
