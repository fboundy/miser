import logging
import asyncio
from contextlib import suppress
from datetime import timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
import homeassistant.util.dt as dt_util
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval, async_track_state_change

from .const import (
    PV_SYSTEM_ENTITIES,
    DOMAIN,
    IMPORT_EXPORT,
    ENTITY_TYPES,
    PLATFORMS,
    CONF_BATTERY_CAPACITY,
    MODEL_BATTERY_MINIMUM_SOC,
    DEFAULT_BATTERY_VOLTAGE,
    CONF_CHARGER_EFFICIENCY,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_POWER,
    CONF_OPTIMISER_FREQUENCY,
    MODEL_ENTITIES_AVAILABLE_WAIT,
    COST_ENTITY_OBJECTS,
    CONTROL_STATE,
    TARIFF_SOURCE_ACCOUNT,
    TARIFF_SOURCE_CODES,
    TARIFF_SOURCE_ENTITIES,
    TARIFF_SOURCE_OCTOPUS_ENERGY,
)
from .entity_tariff import PRICE_UNIT_AUTO, EntityTariff
from .inverters import get_inverter_controller_class
from .octopus import get_octopus_info_from_account, get_octopus_integration_data, Tariff
from .utils import (
    entity_template_candidates,
    get_instance_id,
    get_integration_entities,
    get_value,
    get_key_for_entity,
    infer_integration_device_name,
    log_config_entry,
    redact_sensitive,
)
from .pv_model import InverterModel, BatteryModel, PVsystemModel
from .optimiser import (
    AXLE_VPP_DOMAIN,
    AXLE_VPP_END_ENTITIES,
    AXLE_VPP_START_ENTITIES,
    axle_vpp_control_window,
    optimise,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")
VERSION = "0.0.1"
AXLE_VPP_REFRESH_INTERVAL = timedelta(minutes=1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Miser PV System Optimiser from a config entry.
    """

    existing_data = hass.data.get(DOMAIN)
    if (
        existing_data
        and existing_data.get("entry_id") == entry.entry_id
        and not existing_data.get("unloading")
    ):
        entry.runtime_data = existing_data
        if existing_data.get("setup_complete"):
            _LOGGER.debug("Refreshing duplicate Miser setup call for already loaded entry %s", entry.entry_id)
            await _schedule_optimiser(hass)
            await _setup_config_callbacks(hass)
            await _setup_axle_vpp_callbacks(hass)
            return True
        if existing_data.get("setup_in_progress"):
            _LOGGER.debug("Deferring duplicate Miser setup call while entry %s is still loading", entry.entry_id)
            raise ConfigEntryNotReady("Miser setup is already in progress")

    uuid = await get_instance_id(hass)
    _LOGGER.debug(f"UUID: {uuid}")
    entry.runtime_data = {
        "uuid": uuid,
        "entry_id": entry.entry_id,
        "setup_in_progress": True,
    }
    hass.data[DOMAIN] = entry.runtime_data

    log_config_entry(entry)

    for entity_type in ENTITY_TYPES:
        hass.data[DOMAIN][entity_type] = {}

    platforms_forwarded = False
    try:
        # Forward entries to platform setup
        _LOGGER.debug("Forwarding config entries to platforms: switch, number.")
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        platforms_forwarded = True

        """
        Check that all the required entities are available for the selected inverter controller
        and intantiate the PV model.

        Save the PV model to hass.data[DOMAIN]
        """
        entities_available = await _get_entities(hass, entry)

        if not entities_available:
            raise ConfigEntryNotReady("Could not retrieve necessary entities to set up inverter model")

        _log_all_entities(hass)
        pv_model = await _load_pv_system_model(hass)

        # Load the tariffs
        # Check if we are using the OE integration:
        octopus = await _get_octopus_info(hass, entry)

        if not (pv_model and octopus):
            raise ConfigEntryNotReady("Could not retrieve necessary PV model or tariff data")

        _LOGGER.debug(redact_sensitive(hass.data[DOMAIN].get("octopus_info")))
        hass.data[DOMAIN]["tariff_refresher"] = partial(_refresh_octopus_tariffs, hass, entry)

        # Set up the schedule for the optimise
        await _schedule_optimiser(hass)

        # Set up callbacks for when the config entities change
        await _setup_config_callbacks(hass)
        await _setup_axle_vpp_callbacks(hass)

        hass.data[DOMAIN]["setup_in_progress"] = False
        hass.data[DOMAIN]["setup_complete"] = True
        return True
    except ConfigEntryNotReady as err:
        # Home Assistant retries these with back-off; say why each time so a
        # retry loop is visible in the log rather than a silent 27-hour outage.
        _LOGGER.warning("Miser setup not ready, Home Assistant will retry: %s", err)
        if platforms_forwarded:
            with suppress(Exception):
                await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    except Exception:
        if platforms_forwarded:
            with suppress(Exception):
                await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        _LOGGER.exception("Miser setup failed")
        raise
    finally:
        # Also runs on cancellation, which `except Exception` does not catch.
        # A setup_in_progress flag left True makes every retry raise
        # ConfigEntryNotReady("already in progress") until HA is restarted.
        data = hass.data.get(DOMAIN, {})
        if data.get("entry_id") == entry.entry_id and not data.get("setup_complete"):
            data["setup_in_progress"] = False
            data["setup_complete"] = False


async def _get_octopus_info(hass: HomeAssistant, entry: ConfigEntry):
    if entry.options.get("tariff_source", "") == TARIFF_SOURCE_ENTITIES:
        return _set_entity_tariffs(hass, entry)

    if entry.options.get("tariff_source", "") == TARIFF_SOURCE_OCTOPUS_ENERGY:
        _LOGGER.debug("Loading data from OE integration")
        octopus_info = await get_octopus_integration_data(hass) or {}
    elif entry.options.get("tariff_source", "") == TARIFF_SOURCE_ACCOUNT:
        _LOGGER.debug("Loading Octopus data from API using account details")
        octopus_info = await get_octopus_info_from_account(
            hass, entry.data.get("account_id", None), entry.data.get("api_key", None)
        ) or {}
    elif entry.options.get("tariff_source", "") == TARIFF_SOURCE_CODES:
        octopus_info = {
            "tariff_code": {direction: entry.options.get(f"{direction}_code", None) for direction in IMPORT_EXPORT}
        }
    else:
        octopus_info = {}
        _LOGGER.debug(f"No octopus data found. Tariff source: {entry.options.get('tariff_source', '')}")

    tariff_codes = {
        direction: tariff_code
        for direction, tariff_code in octopus_info.get("tariff_code", {}).items()
        if tariff_code
    }
    if not tariff_codes.get("import"):
        raise ConfigEntryNotReady("No import tariff code available")

    # Build the new set before swapping it in, so a failure part-way through a
    # refresh leaves the working tariffs in place. Reuse unchanged tariffs.
    existing = hass.data[DOMAIN].get("tariffs") or {}
    tariffs = {}
    for direction, tariff_code in tariff_codes.items():
        current = existing.get(direction)
        if current is not None and current.tariff_code == tariff_code:
            tariffs[direction] = current
            continue
        if current is not None:
            _LOGGER.info("Octopus %s tariff changed: %s -> %s", direction, current.tariff_code, tariff_code)
        tariffs[direction] = await Tariff.create(tariff_code, export=(direction == "export"))

    hass.data[DOMAIN]["octopus_info"] = octopus_info
    hass.data[DOMAIN]["tariffs"] = tariffs
    hass.data[DOMAIN]["tariffs_checked_at"] = dt_util.utcnow()
    return True


def _set_entity_tariffs(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Use Home Assistant entities for prices; they are read fresh on every run."""
    price_unit = entry.options.get("price_unit", PRICE_UNIT_AUTO)
    tariffs = {}
    for direction in IMPORT_EXPORT:
        entity_ids = entry.options.get(f"{direction}_entities") or []
        if entity_ids:
            tariffs[direction] = EntityTariff(hass, entity_ids, export=(direction == "export"), price_unit=price_unit)
    if "import" not in tariffs:
        raise ConfigEntryNotReady("No import price entity configured")

    hass.data[DOMAIN]["octopus_info"] = {
        "tariff_code": {direction: tariff.tariff_code for direction, tariff in tariffs.items()}
    }
    hass.data[DOMAIN]["tariffs"] = tariffs
    hass.data[DOMAIN]["tariffs_checked_at"] = dt_util.utcnow()
    return True


async def _refresh_octopus_tariffs(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-read tariff codes so a tariff switch is picked up without a reload."""
    try:
        await _get_octopus_info(hass, entry)
    except Exception as err:  # noqa: BLE001 - keep the working tariffs on any failure
        hass.data[DOMAIN]["tariffs_checked_at"] = dt_util.utcnow()
        _LOGGER.warning("Unable to refresh Octopus tariff codes; keeping current tariffs: %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.
    """
    data = hass.data.get(DOMAIN, {})
    data["unloading"] = True

    for handle_key in ("optimiser_initial_schedule", "optimiser_schedule", "axle_vpp_refresh_callback"):
        unsubscribe = data.pop(handle_key, None)
        if unsubscribe is not None:
            unsubscribe()
            _LOGGER.debug("Cancelled %s", handle_key)

    for unsubscribe in data.pop("control_compliance_callbacks", []):
        unsubscribe()
    _LOGGER.debug("Cancelled Miser control compliance callbacks")

    for unsubscribe in data.pop("config_callbacks", []):
        unsubscribe()
    _LOGGER.debug("Cancelled Miser config callbacks")

    for unsubscribe in data.pop("axle_vpp_callbacks", []):
        unsubscribe()
    _LOGGER.debug("Cancelled Miser Axle VPP callbacks")

    for unsubscribe in data.pop("axle_vpp_boundary_callbacks", []):
        unsubscribe()
    _LOGGER.debug("Cancelled Miser Axle VPP boundary callbacks")
    for unsubscribe in data.pop("axle_vpp_discharge_callbacks", []):
        unsubscribe()
    _LOGGER.debug("Cancelled Miser Axle VPP discharge callbacks")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)
    else:
        data["unloading"] = False

    return unload_ok


async def _get_entities(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Saves required entities to hass.data[DOMAIN]["entities"] from the entity register
    """
    brand = entry.data["inverter_brand"]
    integration = entry.options["integration"]

    _LOGGER.debug(f"Checking entities for inverter brand {brand} with integration {integration}")
    try:
        inverter_controller_class = get_inverter_controller_class(brand, integration)
    except KeyError as err:
        raise ConfigEntryError(f"Miser has no controller for {brand} inverters via {integration}") from err

    # A plain error here marks setup failed for good; NotReady makes HA retry
    # once the inverter integration is set up and has registered its entities.
    integration_data, integration_entities = await get_integration_entities(hass=hass, integration=integration)
    if not integration_entities:
        raise ConfigEntryNotReady(f"No entities found for the {integration} integration; is it set up?")
    inverter_controller = inverter_controller_class(
        hass=hass,
        integration_data=integration_data,
    )
    entity_defs = inverter_controller.entity_defs
    entity_ids = [entity.entity_id for entity in integration_entities]
    integration_device_name = infer_integration_device_name(
        entity_ids=entity_ids,
        entity_defs=entity_defs,
    )
    _LOGGER.debug(f"Integration device name: {integration_device_name}")
    if integration_device_name is None:
        return False

    hass.data[DOMAIN]["integration_data"] = integration_data
    hass.data[DOMAIN]["inverter_controller"] = inverter_controller
    index_lookup = {entity_id: i for i, entity_id in enumerate(entity_ids)}

    success = True

    for entity_type in ENTITY_TYPES:
        entity_ids = entity_defs.get(entity_type, {})
        for key, template in entity_ids.items():
            expected_entity_ids = entity_template_candidates(template, integration_device_name)
            str_log = f"  {key:35s}: {expected_entity_ids[0]:50s} "
            index = next((index_lookup.get(entity_id) for entity_id in expected_entity_ids if entity_id in index_lookup), None)
            if index is None:
                str_log += "Not found"
                if len(expected_entity_ids) > 1:
                    str_log += f" (tried {expected_entity_ids})"

                # We may not need all the control entities so only fail if the main entities aren't all there
                if entity_type == "model_entities":
                    success = False

            else:
                str_log += f"index: {index:4d}"
                entity_id = integration_entities[index].entity_id
                hass.data[DOMAIN][entity_type][key] = entity_id

            _LOGGER.debug(str_log)

    return success


async def _load_pv_system_model(hass: HomeAssistant) -> bool:
    # Read the inverter model parameters from the relevant config entities
    _LOGGER.debug("Loading PV system model")
    kw_inverter = {
        kw: await get_value(hass, kw)
        for kw in [
            CONF_INVERTER_EFFICIENCY,
            CONF_CHARGER_EFFICIENCY,
            CONF_INVERTER_POWER,
            CONF_CHARGER_POWER,
        ]
    }

    # Load the inverter model
    hass.data[DOMAIN]["inverter_model"] = InverterModel(**kw_inverter)

    # Read the battery model parameters from the relevant config entities.
    # Current limit is derived so the battery power limit matches configured charger power.
    kw_battery = {
        kw: await get_value(hass, kw)
        for kw in [CONF_BATTERY_CAPACITY, MODEL_BATTERY_MINIMUM_SOC]
    }
    kw_battery["battery_current_limit"] = kw_inverter[CONF_CHARGER_POWER] / DEFAULT_BATTERY_VOLTAGE

    # Load the battery model
    hass.data[DOMAIN]["battery_model"] = BatteryModel(**kw_battery)

    # Load the PV system model
    hass.data[DOMAIN]["model"] = PVsystemModel(
        inverter=hass.data[DOMAIN]["inverter_model"], battery=hass.data[DOMAIN]["battery_model"]
    )

    return True


async def _schedule_optimiser(hass):
    if hass.data.get(DOMAIN, {}).get("unloading"):
        _LOGGER.debug("Skipping optimiser schedule while Miser is unloading")
        return

    optimiser_minutes = await get_value(hass=hass, key=CONF_OPTIMISER_FREQUENCY, default_value=10)
    _LOGGER.debug(f"Optimiser frequency: {optimiser_minutes} minutes")
    optimiser_interval = timedelta(minutes=optimiser_minutes)

    try:
        # Cancel the current schedule if it exists
        for handle_key in ("optimiser_initial_schedule", "optimiser_schedule"):
            if DOMAIN in hass.data and handle_key in hass.data[DOMAIN]:
                hass.data[DOMAIN].pop(handle_key)()
                _LOGGER.debug("Previous %s cancelled.", handle_key)

        # Get the current time
        now = dt_util.utcnow()

        # Calculate the next aligned run time
        aligned_next_run = now.replace(second=0, microsecond=0) + (
            optimiser_interval - timedelta(minutes=now.minute % (optimiser_interval.total_seconds() // 60))
        )

        # Add a delay for the initial run (2 minutes from now)
        initial_delay = MODEL_ENTITIES_AVAILABLE_WAIT  # 2 minutes in seconds
        _LOGGER.debug(f"Initial run of _optimise scheduled in {initial_delay} seconds.")

        # Define a wrapper function for the initial run
        async def initial_run():
            if hass.data.get(DOMAIN, {}).get("unloading"):
                _LOGGER.debug("Skipping initial _optimise run while Miser is unloading")
                return

            await _wait_for_initial_entities(hass)
            await optimise(hass)
            if hass.data.get(DOMAIN, {}).get("unloading"):
                _LOGGER.debug("Skipping recurring optimiser schedule while Miser is unloading")
                return

            _LOGGER.debug("Initial run of _optimise completed.")

            # Calculate the next aligned run time after the initial run
            now = dt_util.utcnow()
            next_run = aligned_next_run
            if now >= aligned_next_run:
                next_run += (
                    optimiser_interval  # Align to the next interval if the initial run is past the aligned time
                )

            _LOGGER.debug(f"Next aligned run of _optimise scheduled at {next_run}.")

            # Schedule the recurring runs
            hass.data[DOMAIN]["optimiser_schedule"] = async_track_time_interval(
                hass, partial(optimise, hass), optimiser_interval
            )
            _LOGGER.debug("Recurring async_track_time_interval successfully set up.")

        # Schedule the initial run after 2 minutes
        hass.data[DOMAIN]["optimiser_initial_schedule"] = hass.loop.call_later(
            initial_delay,
            lambda: hass.async_create_task(initial_run()),
        ).cancel

    except Exception as e:
        _LOGGER.error(f"Failed to set up _schedule_optimiser: {e}")


async def _wait_for_initial_entities(hass: HomeAssistant) -> None:
    inverter_controller = hass.data[DOMAIN].get("inverter_controller")
    if inverter_controller is None:
        return

    while not hass.data.get(DOMAIN, {}).get("unloading"):
        if await inverter_controller.is_online():
            return

        await _write_status(hass, "Awaiting Sensors")
        _LOGGER.debug("Initial optimiser run waiting for inverter sensor entities")
        await asyncio.sleep(5)


async def _write_status(hass: HomeAssistant, state: str) -> None:
    entity = hass.data.get(DOMAIN, {}).get(COST_ENTITY_OBJECTS, {}).get(CONTROL_STATE)
    if entity is not None:
        await entity.async_set_native_value(state)


async def _setup_config_callbacks(hass):
    for unsubscribe in hass.data[DOMAIN].pop("config_callbacks", []):
        unsubscribe()
    callbacks = []
    for entity_id in hass.data[DOMAIN]["config_entities"].values():
        callback = partial(_state_change_callback, hass)
        callbacks.append(async_track_state_change(hass, entity_id, callback))
    hass.data[DOMAIN]["config_callbacks"] = callbacks
    return True


async def _setup_axle_vpp_callbacks(hass: HomeAssistant) -> bool:
    data = hass.data[DOMAIN]
    unsubscribe = data.pop("axle_vpp_refresh_callback", None)
    if unsubscribe is not None:
        unsubscribe()

    await _refresh_axle_vpp_callbacks(hass, optimise_on_window_change=False)
    data["axle_vpp_refresh_callback"] = async_track_time_interval(
        hass,
        _axle_vpp_refresh_callback(hass),
        AXLE_VPP_REFRESH_INTERVAL,
    )
    _LOGGER.debug("Axle VPP refresh callback set up")
    return True


async def _refresh_axle_vpp_callbacks(hass: HomeAssistant, optimise_on_window_change: bool = False) -> None:
    data = hass.data.get(DOMAIN, {})
    if data.get("unloading"):
        return

    if not hass.config_entries.async_entries(AXLE_VPP_DOMAIN):
        if data.get("axle_vpp_installed"):
            _LOGGER.info("Axle VPP integration is no longer available; Miser retains inverter control")
        data["axle_vpp_installed"] = False
        _cancel_axle_vpp_event_callbacks(data)
        return

    was_installed = data.get("axle_vpp_installed")
    data["axle_vpp_installed"] = True
    if not was_installed:
        _LOGGER.info("Axle VPP integration detected; Miser will price Axle event export at the VPP rate")

    if not data.get("axle_vpp_callbacks"):
        callbacks = []
        for entity_id in AXLE_VPP_START_ENTITIES + AXLE_VPP_END_ENTITIES:
            callbacks.append(async_track_state_change(hass, entity_id, _axle_vpp_state_change_callback(hass)))
        data["axle_vpp_callbacks"] = callbacks
        _LOGGER.debug("Registered Axle VPP state-change callbacks")

    previous_window_key = data.get("axle_vpp_window_key")
    window = _schedule_axle_vpp_boundary_callbacks(hass)
    window_key = data.get("axle_vpp_window_key")
    if (
        optimise_on_window_change
        and window is not None
        and window_key != previous_window_key
        and dt_util.utcnow() >= window["event_start"].to_pydatetime()
    ):
        _LOGGER.info("Axle VPP window discovered inside the active event; re-running optimiser")
        await optimise(hass=hass)


def _cancel_axle_vpp_event_callbacks(data: dict) -> None:
    for unsubscribe in data.pop("axle_vpp_callbacks", []):
        unsubscribe()
    for unsubscribe in data.pop("axle_vpp_boundary_callbacks", []):
        unsubscribe()
    for unsubscribe in data.pop("axle_vpp_discharge_callbacks", []):
        unsubscribe()
    data["axle_vpp_window_key"] = None


def _axle_vpp_refresh_callback(hass: HomeAssistant):
    async def _callback(_now):
        await _refresh_axle_vpp_callbacks(hass, optimise_on_window_change=True)

    return _callback


def _axle_vpp_state_change_callback(hass: HomeAssistant):
    async def _callback(entity_id, old_state, new_state):
        if hass.data.get(DOMAIN, {}).get("unloading"):
            return

        _LOGGER.debug(
            "Axle VPP entity %s changed from %s to %s",
            entity_id,
            old_state.state if old_state else None,
            new_state.state if new_state else None,
        )
        await _refresh_axle_vpp_callbacks(hass, optimise_on_window_change=False)
        await optimise(hass=hass)

    return _callback


def _schedule_axle_vpp_boundary_callbacks(hass: HomeAssistant) -> dict | None:
    data = hass.data.get(DOMAIN, {})
    window = axle_vpp_control_window(hass)
    if window is None:
        for unsubscribe in data.pop("axle_vpp_boundary_callbacks", []):
            unsubscribe()
        for unsubscribe in data.pop("axle_vpp_discharge_callbacks", []):
            unsubscribe()
        data["axle_vpp_boundary_callbacks"] = []
        data["axle_vpp_discharge_callbacks"] = []
        data["axle_vpp_window_key"] = None
        return None

    window_key = f"{window['event_start'].isoformat()}-{window['event_end'].isoformat()}"
    if data.get("axle_vpp_window_key") == window_key and "axle_vpp_boundary_callbacks" in data:
        return window

    for unsubscribe in data.pop("axle_vpp_boundary_callbacks", []):
        unsubscribe()
    for unsubscribe in data.pop("axle_vpp_discharge_callbacks", []):
        unsubscribe()

    callbacks = []
    now = dt_util.utcnow()
    data["axle_vpp_window_key"] = window_key
    for boundary in ["event_start", "event_end"]:
        check_at = window[boundary].to_pydatetime()
        if check_at <= now:
            continue

        callbacks.append(
            async_track_point_in_utc_time(
                hass,
                _axle_vpp_boundary_callback(hass, boundary),
                check_at,
            )
        )

    data["axle_vpp_boundary_callbacks"] = callbacks
    data["axle_vpp_discharge_callbacks"] = []
    if callbacks:
        _LOGGER.debug(
            "Scheduled %d Axle VPP optimiser callbacks for %s - %s",
            len(callbacks),
            window["event_start"].isoformat(),
            window["event_end"].isoformat(),
        )
    return window


def _axle_vpp_boundary_callback(hass: HomeAssistant, boundary: str):
    async def _callback(_now):
        if hass.data.get(DOMAIN, {}).get("unloading"):
            return

        _LOGGER.info("Axle VPP %s reached; re-running optimiser", boundary)
        _schedule_axle_vpp_boundary_callbacks(hass)
        await optimise(hass=hass)

    return _callback


async def _state_change_callback(hass, entity_id, old_state, new_state):
    """Callback function triggered when the config entity state changes."""
    if hass.data.get(DOMAIN, {}).get("unloading"):
        _LOGGER.debug("Ignoring config callback while Miser is unloading")
        return

    _LOGGER.debug(
        f"Entity {entity_id} changed from {old_state.state if old_state else 'None'} to {new_state.state if new_state else 'None'}"
    )

    key = get_key_for_entity(hass=hass, entity_id=entity_id)
    if key == CONF_OPTIMISER_FREQUENCY:
        await optimise(hass=hass)
        _LOGGER.debug("Reset optimiser frequency")
        await _schedule_optimiser(hass)
    elif key in PV_SYSTEM_ENTITIES:
        _LOGGER.debug("Reinitialise PV sytem")
        await _load_pv_system_model(hass)
        await optimise(hass=hass)

    else:
        await optimise(hass=hass)


def _log_all_entities(hass: HomeAssistant) -> None:
    for entity_type in ENTITY_TYPES:
        _LOGGER.debug(f"{entity_type}:")
        _LOGGER.debug(f"{'-'*(len(entity_type)+1)}")
        for entity in hass.data[DOMAIN][entity_type]:
            entity_id = hass.data[DOMAIN][entity_type].get(entity, None)
            str_log = f"{entity:35s}:"
            if entity_id is not None:
                state = hass.states.get(entity_id)
                if state is not None:
                    str_log += f"{entity_id:35s} {state.state}"
                else:
                    str_log += f"{entity_id:35s} <=== NONE!"
            else:
                str_log += f"<=== MISSING!"

            _LOGGER.debug(str_log)
        _LOGGER.debug("")
