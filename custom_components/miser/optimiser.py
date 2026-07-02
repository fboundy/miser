import logging
import asyncio
import pandas as pd
from numpy import arange
from datetime import datetime
from math import isfinite
from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import history
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
import homeassistant.util.dt as dt_util

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
    CONF_OPTIMISER_FREQUENCY,
    CONF_WHOLE_HORIZON_BETA,
    COST_ENTITY_OBJECTS,
    CONTROL_FORCE_CURRENT,
    CONTROL_FORCE_POWER,
    CONTROL_NEXT_SLOT_END,
    CONTROL_NEXT_SLOT_POWER,
    CONTROL_NEXT_SLOT_START,
    CONTROL_NEXT_SLOT_TARGET_SOC,
    CONTROL_STATE,
    CONTROL_TARGET_SOC,
    MODEL_CONSUMPTION_TODAY,
    MODEL_BATTERY_SOC,
    MODEL_ENTITIES_AVAILABLE_WAIT,
    OPTIMISER_MAX_ITERS,
)
from .utils import get_value, get_entity_for_key, redact_sensitive

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")
CONTROL_COMPLIANCE_DELAY_SECONDS = 60


def _is_finite(value) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def cl_to_weights(cl):
    wt90 = min(max(cl - 50, 0) / 40, 1)
    wt10 = min(max(50 - cl, 0) / 40, 1)
    wt50 = 1 - wt90 - wt10
    return wt50, wt10, wt90


async def optimise(hass: HomeAssistant, now=None):
    data = hass.data.get(DOMAIN)
    if not data or data.get("unloading"):
        _LOGGER.debug("Skipping optimise because Miser is unloading or unloaded")
        return
    await _write_status(hass, "Optimising")

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
        await _write_status(hass, "Awaiting Sensors")
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

    model.whole_horizon_slots = []
    model.whole_horizon_cost = None
    model.whole_horizon_flows = None
    whole_horizon_beta = await get_value(hass, CONF_WHOLE_HORIZON_BETA)
    if whole_horizon_beta:
        model.whole_horizon_slots = await model.whole_horizon()
        model.whole_horizon_cost, model.whole_horizon_flows = await _calculate_cost_and_flows(
            model,
            model.whole_horizon_slots,
        )
        _LOGGER.debug(f"Whole-horizon cost: {model.whole_horizon_cost:6.1f}")
        cost_keys.append("whole_horizon_cost")

    finite_cost_keys = [key for key in cost_keys if _is_finite(getattr(model, key, None))]
    if not finite_cost_keys:
        _LOGGER.warning("No finite optimiser costs were produced; skipping optimiser output")
        await _write_status(hass, "Idle")
        return

    optimised_key = min(finite_cost_keys, key=lambda key: getattr(model, key))
    model.optimised_cost = getattr(model, optimised_key)
    model.optimised_slots = list(getattr(model, optimised_key.replace("_cost", "_slots")))
    model.optimised_flows = getattr(model, optimised_key.replace("_cost", "_flows"))
    model.best_cost = model.optimised_cost
    _LOGGER.debug(f"Optimised cost: {model.optimised_cost:6.1f} ({optimised_key})")

    if _is_unloading(hass):
        _LOGGER.debug("Skipping optimiser output because Miser is unloading")
        return

    await _write_cost_entities(hass, model)
    if _is_unloading(hass):
        _LOGGER.debug("Skipping inverter control because Miser is unloading")
        return

    await _apply_inverter_control(hass, model)


async def _write_status(hass: HomeAssistant, state: str) -> None:
    sensor_entities = hass.data.get(DOMAIN, {}).get(COST_ENTITY_OBJECTS, {})
    entity = sensor_entities.get(CONTROL_STATE)
    if entity is not None:
        await entity.async_set_native_value(state)


def _is_unloading(hass: HomeAssistant) -> bool:
    return bool(hass.data.get(DOMAIN, {}).get("unloading"))


async def _write_cost_entities(hass: HomeAssistant, model) -> None:
    cost_entities = hass.data[DOMAIN].get(COST_ENTITY_OBJECTS, {})

    for key in [
        "base_cost",
        "swap_cost",
        "lcc_cost",
        "discharge_cost",
        "fill_first_cost",
        "whole_horizon_cost",
        "optimised_cost",
    ]:
        entity = cost_entities.get(key)
        if entity is not None:
            flows = getattr(model, key.replace("_cost", "_flows"), None)
            await entity.async_set_native_value(
                getattr(model, key, None),
                slots=_serialise_slots(flows, merge=key == "optimised_cost"),
                flows=_serialise_flows(flows),
            )


async def _apply_inverter_control(hass: HomeAssistant, model, schedule_checks: bool = True) -> None:
    inverter_controller = hass.data[DOMAIN].get("inverter_controller")
    if inverter_controller is None:
        _LOGGER.warning("No inverter controller is available; unable to apply optimised controls")
        await _write_status(hass, "Idle")
        return

    control_slots = _control_slots(model.optimised_flows)
    now = pd.Timestamp.now(tz="UTC")
    current_slot = _find_current_control_slot(control_slots, now)
    desired_state = current_slot["state"] if current_slot is not None else "idle"
    desired_power = current_slot["power"] if current_slot is not None else 0
    desired_target_soc = current_slot["target_soc"] if current_slot is not None else None
    desired_current = await _power_to_current(inverter_controller, desired_power)

    await _write_control_entities(
        hass,
        state=_display_state(desired_state),
        force_current=desired_current if desired_state != "idle" else 0,
        force_power=desired_power if desired_state != "idle" else 0,
        target_soc=desired_target_soc if desired_state != "idle" else None,
        current_slot=current_slot,
        next_slot=_find_next_control_slot(control_slots, now),
    )

    optimiser_minutes = float(
        await get_value(
            hass,
            CONF_OPTIMISER_FREQUENCY,
            default_value=DEFAULTS[CONF_OPTIMISER_FREQUENCY],
        )
        or DEFAULTS[CONF_OPTIMISER_FREQUENCY]
    )
    apply_window = pd.Timedelta(minutes=optimiser_minutes)
    slots_to_apply = _slots_to_apply(control_slots, now, apply_window)
    if schedule_checks:
        _schedule_control_compliance_checks(hass, control_slots)
    if slots_to_apply:
        _LOGGER.debug(
            "Applying inverter slot 1 controls for: %s",
            ", ".join(
                f"{slot['state']} {slot['start']}-{slot['end']}"
                for slot in slots_to_apply
            ),
        )

    if not slots_to_apply:
        try:
            if not await inverter_controller.control_matches("idle", None, 0):
                _LOGGER.debug("Setting inverter forced control state to idle")
                control_idle = getattr(inverter_controller, "control_idle", None)
                if control_idle is not None:
                    await control_idle()
        except (RuntimeError, HomeAssistantError) as err:
            _LOGGER.warning("Unable to verify or set inverter idle control: %s", err)
        return

    for slot in slots_to_apply:
        try:
            _LOGGER.debug(
                "Applying inverter control: %s %s-%s %.1fW %.2fA target %.1f%%",
                slot["state"],
                slot["start"],
                slot["end"],
                slot["power"],
                await _power_to_current(inverter_controller, abs(slot["power"])),
                slot["target_soc"],
            )
            if slot["state"] == "charging":
                await inverter_controller.control_charge(
                    slot["start"].to_pydatetime(),
                    slot["end"].to_pydatetime(),
                    slot["target_soc"],
                    slot["power"],
                )
            elif slot["state"] == "discharging":
                await inverter_controller.control_discharge(
                    slot["start"].to_pydatetime(),
                    slot["end"].to_pydatetime(),
                    slot["target_soc"],
                    abs(slot["power"]),
                )
        except (RuntimeError, HomeAssistantError) as err:
            _LOGGER.warning("Unable to verify or apply inverter control: %s", err)


def _slots_to_apply(control_slots: list[dict], now: pd.Timestamp, apply_window: pd.Timedelta) -> list[dict]:
    apply_before = now + apply_window
    slots = []
    for state in ["charging", "discharging"]:
        state_slots = [
            slot
            for slot in control_slots
            if slot["state"] == state
            and slot["end"] > now
            and slot["start"] <= apply_before
        ]
        if state_slots:
            slots.append(sorted(state_slots, key=lambda slot: slot["start"])[0])
    return sorted(slots, key=lambda slot: slot["start"])


def _display_state(state: str) -> str:
    return {
        "idle": "Idle",
        "charging": "Charging",
        "discharging": "Discharging",
    }.get(state, state)


def _schedule_control_compliance_checks(hass: HomeAssistant, control_slots: list[dict]) -> None:
    data = hass.data.get(DOMAIN, {})
    for unsubscribe in data.pop("control_compliance_callbacks", []):
        unsubscribe()

    now = pd.Timestamp.now(tz="UTC")
    delay = pd.Timedelta(seconds=CONTROL_COMPLIANCE_DELAY_SECONDS)
    callbacks = []

    for slot in control_slots:
        for boundary in ["start", "end"]:
            check_at = slot[boundary] + delay
            if check_at <= now:
                continue

            callbacks.append(
                async_track_point_in_utc_time(
                    hass,
                    _control_compliance_callback(hass, slot, boundary),
                    check_at.to_pydatetime(),
                )
            )

    data["control_compliance_callbacks"] = callbacks
    if callbacks:
        _LOGGER.debug("Scheduled %d inverter compliance checks", len(callbacks))


def _control_compliance_callback(hass: HomeAssistant, slot: dict, boundary: str):
    async def _callback(_now):
        data = hass.data.get(DOMAIN, {})
        if data.get("unloading"):
            return

        _LOGGER.debug(
            "Checking inverter compliance after %s of %s slot %s-%s",
            boundary,
            slot["state"],
            slot["start"],
            slot["end"],
        )
        model = data.get("model")
        if model is not None:
            await _apply_inverter_control(hass, model, schedule_checks=False)

    return _callback


async def _write_control_entities(
    hass: HomeAssistant,
    state: str,
    force_current: float | None,
    force_power: float | None,
    target_soc: float | None,
    current_slot: dict | None,
    next_slot: dict | None,
) -> None:
    sensor_entities = hass.data[DOMAIN].get(COST_ENTITY_OBJECTS, {})
    attributes = {
        "current_slot": _serialise_control_slot(current_slot),
        "next_slot": _serialise_control_slot(next_slot),
    }

    updates = {
        CONTROL_STATE: state,
        CONTROL_FORCE_CURRENT: force_current,
        CONTROL_FORCE_POWER: force_power,
        CONTROL_TARGET_SOC: target_soc,
        CONTROL_NEXT_SLOT_START: _slot_value(next_slot, "start", local_time=True),
        CONTROL_NEXT_SLOT_END: _slot_value(next_slot, "end", local_time=True),
        CONTROL_NEXT_SLOT_POWER: _slot_value(next_slot, "power"),
        CONTROL_NEXT_SLOT_TARGET_SOC: _slot_value(next_slot, "target_soc"),
    }

    for key, value in updates.items():
        entity = sensor_entities.get(key)
        if entity is not None:
            await entity.async_set_native_value(value, attributes=attributes)


def _control_slots(flows: pd.DataFrame | None) -> list[dict]:
    if flows is None:
        return []

    forced_flows = flows[flows["forced"] != 0]
    if forced_flows.empty:
        return []

    slots = []
    current_start = None
    current_end = None
    current_powers = []
    current_target_soc = None

    for start, row in forced_flows.iterrows():
        power = float(row.get("forced"))
        end = start + pd.Timedelta(hours=float(row.get("dt_hours")))

        if (
            current_start is None
            or start != current_end
            or not _same_force_direction(power, current_powers[-1])
            or not _within_power_tolerance(power, sum(current_powers) / len(current_powers))
        ):
            if current_start is not None:
                slots.append(_build_control_slot(current_start, current_end, current_powers, current_target_soc))

            current_start = start
            current_end = end
            current_powers = [power]
            current_target_soc = float(row.get("soc_end"))
            continue

        current_end = end
        current_powers.append(power)
        current_target_soc = float(row.get("soc_end"))

    if current_start is not None:
        slots.append(_build_control_slot(current_start, current_end, current_powers, current_target_soc))

    return slots


def _build_control_slot(start, end, powers: list[float], target_soc: float) -> dict:
    power = sum(powers) / len(powers)
    return {
        "start": start,
        "end": end,
        "state": "charging" if power > 0 else "discharging",
        "power": power,
        "target_soc": target_soc,
    }


def _find_current_control_slot(control_slots: list[dict], now: pd.Timestamp) -> dict | None:
    for slot in control_slots:
        if slot["start"] <= now < slot["end"]:
            return slot
    return None


def _find_next_control_slot(control_slots: list[dict], now: pd.Timestamp) -> dict | None:
    future_slots = [slot for slot in control_slots if slot["start"] > now]
    if not future_slots:
        return None
    return min(future_slots, key=lambda slot: slot["start"])


def _same_force_direction(power: float, other_power: float) -> bool:
    return (power > 0) == (other_power > 0)


async def _power_to_current(inverter_controller, power: float) -> float | None:
    power_to_current = getattr(inverter_controller, "power_to_current", None)
    if power_to_current is None:
        return None
    return await power_to_current(power)


def _serialise_control_slot(slot: dict | None) -> dict | None:
    if slot is None:
        return None

    return {
        "start": slot["start"].isoformat() if hasattr(slot["start"], "isoformat") else slot["start"],
        "end": slot["end"].isoformat() if hasattr(slot["end"], "isoformat") else slot["end"],
        "state": slot["state"],
        "power": _serialise_number(slot["power"]),
        "target_soc": _serialise_number(slot["target_soc"]),
    }


def _slot_value(slot: dict | None, key: str, local_time: bool = False):
    if slot is None:
        return None

    value = slot.get(key)
    if hasattr(value, "isoformat"):
        if local_time:
            value = dt_util.as_local(value)
        return value.isoformat()
    return value


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

        if not _is_finite(discharge_cost) or not _is_finite(best_cost) or discharge_cost >= best_cost:
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
    terminal_penalty = _terminal_battery_penalty(model, flows)
    cost = net_cost.sum() + terminal_penalty
    if not _is_finite(cost):
        _LOGGER.warning("Calculated non-finite optimiser cost; rejecting candidate")
        return float("inf"), flows
    if terminal_penalty:
        _LOGGER.debug(
            "Terminal battery penalty: %6.1fp, terminal SOC: %5.1f%%",
            terminal_penalty,
            flows["soc_end"].iloc[-1],
        )
    return cost, flows


def _terminal_battery_penalty(model, flows: pd.DataFrame) -> float:
    valuation_prices = getattr(model, "valuation_prices", None)
    if flows is None or flows.empty or valuation_prices is None or valuation_prices.empty:
        return 0.0

    terminal_energy = float(flows["chg_end"].iloc[-1])
    target_energy = float(model.initial_soc) / 100 * model.battery.capacity
    if not _is_finite(terminal_energy) or not _is_finite(target_energy):
        return 0.0
    deficit_wh = max(target_energy - terminal_energy, 0)
    if deficit_wh <= 0:
        return 0.0

    charge_power = min(model.battery.max_charge_power, model.inverter.charger_power)
    remaining_wh = deficit_wh
    penalty = 0.0

    prices = valuation_prices.copy()
    if "dt_hours" not in prices.columns:
        prices["dt_hours"] = _price_dt_hours(prices)

    for _start, row in prices.sort_values("import").iterrows():
        if remaining_wh <= 0:
            break

        dt_hours = float(row["dt_hours"])
        if not _is_finite(dt_hours) or not _is_finite(row["import"]):
            continue
        battery_wh = min(remaining_wh, charge_power * dt_hours * model.inverter.charger_efficiency)
        grid_kwh = battery_wh / model.inverter.charger_efficiency / 1000
        penalty += grid_kwh * float(row["import"])
        remaining_wh -= battery_wh

    if remaining_wh > 0:
        fallback_price = float(prices["import"].max())
        if not _is_finite(fallback_price):
            return penalty
        grid_kwh = remaining_wh / model.inverter.charger_efficiency / 1000
        penalty += grid_kwh * fallback_price

    return penalty


def _price_dt_hours(prices: pd.DataFrame) -> pd.Series:
    if len(prices.index) < 2:
        return pd.Series(index=prices.index, data=MODEL_PERIOD_MINUTES / 60)

    dt_hours = -prices.index.to_series().diff(-1) / pd.Timedelta("60min")
    return dt_hours.ffill().fillna(MODEL_PERIOD_MINUTES / 60)


def _serialise_slots(flows: pd.DataFrame | None, merge: bool = False) -> list[dict]:
    if flows is None:
        return []

    forced_flows = flows[flows["forced"] != 0]
    if merge:
        return _merge_slots(forced_flows)

    serialised = []
    for start, row in forced_flows.iterrows():
        serialised.append(
            {
                "start": start.isoformat() if hasattr(start, "isoformat") else start,
                "power": _serialise_number(row.get("forced")),
            }
        )
    return serialised


def _merge_slots(flows: pd.DataFrame) -> list[dict]:
    if flows.empty:
        return []

    merged = []
    current_start = None
    current_end = None
    current_powers = []

    for start, row in flows.iterrows():
        power = float(row.get("forced"))
        end = start + pd.Timedelta(hours=float(row.get("dt_hours")))

        if (
            current_start is None
            or start != current_end
            or not _within_power_tolerance(power, sum(current_powers) / len(current_powers))
        ):
            if current_start is not None:
                merged.append(_serialise_merged_slot(current_start, current_end, current_powers))

            current_start = start
            current_end = end
            current_powers = [power]
            continue

        current_end = end
        current_powers.append(power)

    if current_start is not None:
        merged.append(_serialise_merged_slot(current_start, current_end, current_powers))

    return merged


def _within_power_tolerance(power: float, reference_power: float) -> bool:
    if power == 0 or reference_power == 0:
        return power == reference_power
    if (power > 0) != (reference_power > 0):
        return False

    return abs(power - reference_power) <= max(abs(power), abs(reference_power)) * 0.10


def _serialise_merged_slot(start, end, powers: list[float]) -> dict:
    return {
        "start": start.isoformat() if hasattr(start, "isoformat") else start,
        "end": end.isoformat() if hasattr(end, "isoformat") else end,
        "power": _serialise_number(sum(powers) / len(powers)),
    }


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

    fallback = None
    if "final" not in consumption.columns:
        fallback = await _fallback_consumption(hass, consumption)
        consumption["final"] = fallback
    else:
        consumption["final"] = pd.to_numeric(consumption["final"], errors="coerce")
        missing_count = int(consumption["final"].isna().sum())
        if missing_count:
            fallback = await _fallback_consumption(hass, consumption)
            valid_count = len(consumption["final"]) - missing_count
            if valid_count < len(consumption["final"]) / 2:
                consumption["final"] = fallback
                source = "replaced with"
            else:
                consumption["final"] = consumption["final"].fillna(fallback)
                source = "filled from"
            _LOGGER.warning(
                "Consumption history from %s left %s/%s model slots empty; %s configured fallback load profile",
                entity_id,
                missing_count,
                len(consumption["final"]),
                source,
            )

    hass.data[DOMAIN]["model"].consumption = consumption["final"].rename("consumption")

    return True


async def _fallback_consumption(hass: HomeAssistant, consumption: pd.DataFrame) -> pd.Series:
    daily_consumption = await get_value(hass, CONF_DAILY_CONSUMPTION_KWH)
    if await get_value(hass, CONF_SHAPE_CONSUMPTION):
        daily = (
            pd.DataFrame(CONSUMPTION_SHAPE)
            .set_index("hours")
            .reindex(arange(0, 24.5, 0.5))
            .interpolate()
            .iloc[:-1]
        )
        daily["fallback"] = daily["consumption"] * daily_consumption / (daily["consumption"].sum() / 2000)
        daily.index = pd.to_datetime(daily.index, unit="h").time
        fallback = consumption[["time_of_day"]].merge(daily["fallback"], "left", left_on="time_of_day", right_index=True)
        return fallback["fallback"].set_axis(consumption.index)

    return pd.Series(index=consumption.index, data=daily_consumption * 1000 / 24)


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
    valuation_end = end + pd.Timedelta(hours=24)
    for direction in IMPORT_EXPORT:
        tariff = hass.data[DOMAIN]["tariffs"].get(direction, None)
        if tariff is not None:
            price[direction] = await tariff.to_df(start=start, end=valuation_end - freq)
            price[direction].rename(columns={"unit": direction}, inplace=True)
            # _LOGGER.debug(f"\n{price[direction]}")
    if "import" not in price:
        raise ValueError("Import tariff prices are required")
    prices = pd.concat(price.values(), axis=1)
    if "export" not in prices.columns:
        prices["export"] = 0
    hass.data[DOMAIN]["model"].prices = prices.loc[start : end - freq]
    hass.data[DOMAIN]["model"].valuation_prices = prices.loc[end : valuation_end - freq]


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
