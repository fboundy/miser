import logging
import pandas as pd
from numpy import arange
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import history
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import parse_datetime as dt_util

from .const import (
    DOMAIN,
    DATETIME_FORMAT_LONG,
    MODEL_DURATION_HOURS,
    MODEL_PERIOD_MINUTES,
    MODEL_COLUMNS,
    DEFAULTS,
    CONSUMPTION_SHAPE,
    SOLCAST_INTEGRATION,
    SOLCAST_PV_KEY,
    SOLCAST_DETAILED_FORECAST_ATTRIBUTE,
    SOLCAST_FORECAST_PERIODS,
    SOLCAST_COLUMNS,
)
from .utils import get_value, get_entity_for_key, log_config_entry
from .octopus import get_octopus_prices_from_api

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

    model.flows = pd.DataFrame(index=index, data={col: 0 for col in MODEL_COLUMNS})
    await _get_consumption(hass=hass, start=start, end=end, freq=freq)
    await _get_solcast(hass=hass, start=start, end=end, freq=freq)
    await _get_prices(hass=hass, start=start, end=end, freq=freq)


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

    use_consumption = await get_value(hass, "USE_CONSUMPTION_HISTORY")
    if use_consumption:
        entity_id = get_entity_for_key(hass, "CONSUMPTION_TODAY")
        history_days = await get_value(hass, "HISTORY_DAYS")
        load_margin = await get_value(hass, "LOAD_MARGIN")
        weekday_weighting = await get_value(hass, "WEEKDAY_WEIGHTING")
        if entity_id is not None:
            _LOGGER.debug(f"Loading {history_days} days consumption history from {entity_id}")
            consumption_history = await _get_hass_power_from_daily_kwh(hass, entity_id, history_days, freq=freq)

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
                _LOGGER.debug(f"Consumption\n{consumption.to_string()}")

            else:
                _LOGGER.debug(
                    f"  - Ignoring 'Day of Week Weighting' because only {history_days} days of history is available"
                )
                consumption["final"] = consumption["mean"]

    if "final" not in consumption.columns:
        # Need to add config entities for manual case
        daily_consumption = await get_value(hass, "DAILY_CONSUMPTION_KWH")
        if get_value("SHAPE_CONSUMPTION"):
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

    hass.data[DOMAIN]["model"].consumption = consumption["final"]

    return True


async def _get_solcast(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    config_entries = hass.config_entries.async_entries(SOLCAST_INTEGRATION)

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

        solcast = pd.DataFrame(forecast).set_index("period_start").sort_index().loc[start:end]
        confidence_level = await get_value(hass, "SOLCAST_CONFIDENCE")
        weights = cl_to_weights(confidence_level)
        solcast["weighted"] = 0
        for weight, col in zip(weights, SOLCAST_COLUMNS):
            solcast["weighted"] += weight * solcast[col] * 1000
        _LOGGER.debug(f"\n{solcast.to_string()}")
    hass.data[DOMAIN]["model"].solar = solcast["weighted"]


async def _get_prices(hass: HomeAssistant, start: pd.Timestamp, end: pd.Timestamp, freq: pd.Timedelta) -> bool:
    _LOGGER.debug(hass.data[DOMAIN]["octopus_info"])
    hass.data[DOMAIN]["model"].prices = await get_octopus_prices_from_api(hass, start, end)


async def _get_hass_power_from_daily_kwh(hass, entity_id, days=DEFAULTS["HISTORY_DAYS"], freq=pd.Timedelta("30min")):
    df = await _hass_to_df(hass, entity_id, days=days)
    if df is not None:
        x = df.diff().clip(0).fillna(0).cumsum() + df.iloc[0]
        x.index = x.index.round("1s")
        x = x[~x.index.duplicated()]
        y = -pd.concat([x.resample("1s").interpolate().resample(freq).asfreq(), x.iloc[-1:]]).diff(-1)
        dt = y.index.diff().total_seconds() / pd.Timedelta("60min").total_seconds() / 1000
        df = y[1:-1] / dt[2:]

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
