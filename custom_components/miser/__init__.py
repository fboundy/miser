import logging
import asyncio
from datetime import timedelta
from functools import partial
from logging.handlers import RotatingFileHandler

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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
    CONF_INVERTER_LOSS,
    CONF_CHARGER_EFFICIENCY,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_POWER,
    CONF_OPTIMISER_FREQUENCY,
    MODEL_ENTITIES_AVAILABLE_WAIT,
    COST_ENTITY_OBJECTS,
    CONTROL_STATE,
)
from .inverters import get_inverter_controller_class
from .octopus import get_octopus_info_from_account, get_octopus_integration_data, Tariff
from .utils import (
    get_instance_id,
    get_integration_entities,
    get_value,
    get_key_for_entity,
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


def setup_custom_logging():
    """
    Configure custom logging to write to a file.
    """
    # Remove existing handlers to avoid duplicates
    for handler in list(_LOGGER.handlers):
        if isinstance(handler, RotatingFileHandler):
            _LOGGER.removeHandler(handler)

    # Set up a rotating file handler
    log_filename = f"/config/{DOMAIN}.log"
    file_handler = RotatingFileHandler(
        log_filename,
        maxBytes=2**10,
        backupCount=3,
        mode="a",  # 1 MB max size, 3 backups
    )

    # Force rotation of the log file
    file_handler.doRollover()

    formatter = logging.Formatter(
        "%(asctime)s - %(module)-10s - %(levelname)-8s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Add handler to the logger
    _LOGGER.addHandler(file_handler)
    _LOGGER.setLevel(logging.DEBUG)
    _LOGGER.propagate = True

    # Test the logging setup
    _LOGGER.debug("Custom logging initialized.")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Miser PV System Optimiser from a config entry.
    """
    setup_custom_logging()

    # Test the logging setup
    _LOGGER.debug("Custom logging initialized.")

    uuid = await get_instance_id(hass)
    _LOGGER.debug(f"UUID: {uuid}")
    entry.runtime_data = {"uuid": uuid}
    hass.data[DOMAIN] = entry.runtime_data

    log_config_entry(entry)

    for entity_type in ENTITY_TYPES:
        hass.data[DOMAIN][entity_type] = {}

    # Forward entries to platform setup
    _LOGGER.debug("Forwarding config entries to platforms: switch, number.")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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

    # Set up the schedule for the optimise
    await _schedule_optimiser(hass)

    # Set up callbacks for when the config entities change
    await _setup_config_callbacks(hass)
    await _setup_axle_vpp_callbacks(hass)

    return True


async def _get_octopus_info(hass: HomeAssistant, entry: ConfigEntry):
    if entry.options.get("tariff_source", "") == "Get tariff codes from Octopus Energy integration":
        _LOGGER.debug("Loading data from OE integration")
        octopus_info = await get_octopus_integration_data(hass) or {}
    elif entry.options.get("tariff_source", "") == "Specify Octopus Account ID and API Key":
        _LOGGER.debug("Loading Octopus data from API using account details")
        octopus_info = await get_octopus_info_from_account(
            hass, entry.data.get("account_id", None), entry.data.get("api_key", None)
        ) or {}
    elif entry.options.get("tariff_source", "") == "Specify Octopus import and export tariff codes directly":
        octopus_info = {
            "tariff_code": {direction: entry.options.get(f"{direction}_code", None) for direction in IMPORT_EXPORT}
        }
    else:
        octopus_info = {}
        _LOGGER.debug(f"No octopus data found. Tariff source: {entry.options.get('tariff_source', '')}")

    hass.data[DOMAIN]["octopus_info"] = octopus_info
    hass.data[DOMAIN]["tariffs"] = {}
    tariff_codes = {
        direction: tariff_code
        for direction, tariff_code in octopus_info.get("tariff_code", {}).items()
        if tariff_code
    }
    if not tariff_codes.get("import"):
        raise ConfigEntryNotReady("No import tariff code available")

    for direction, tariff_code in tariff_codes.items():
        hass.data[DOMAIN]["tariffs"][direction] = await Tariff.create(tariff_code, export=(direction == "export"))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.
    """
    data = hass.data.get(DOMAIN, {})
    data["unloading"] = True

    for handle_key in ("optimiser_initial_schedule", "optimiser_schedule"):
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
    integration_data, integration_entities = await get_integration_entities(hass=hass, integration=integration)
    inverter_controller_class = get_inverter_controller_class(brand, integration)
    inverter_controller = inverter_controller_class(
        hass=hass,
        integration_data=integration_data,
    )
    entity_defs = inverter_controller.entity_defs
    entity_ids = [entity.entity_id for entity in integration_entities]
    integration_device_name = _infer_integration_device_name(
        entity_ids=entity_ids,
        entity_defs=entity_defs,
    )
    _LOGGER.debug(f"Integration device name: {integration_device_name}")

    hass.data[DOMAIN]["integration_data"] = integration_data
    hass.data[DOMAIN]["inverter_controller"] = inverter_controller
    index_lookup = {entity_id: i for i, entity_id in enumerate(entity_ids)}

    success = True

    for entity_type in ENTITY_TYPES:
        entity_ids = entity_defs.get(entity_type, {})
        for key, template in entity_ids.items():
            expected_entity_ids = _entity_template_candidates(template, integration_device_name)
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


def _infer_integration_device_name(entity_ids: list[str], entity_defs: dict) -> str:
    """Infer the entity object prefix used by the selected inverter integration."""
    candidates: dict[str, int] = {}

    for entity_type in ENTITY_TYPES:
        for entity_templates in entity_defs.get(entity_type, {}).values():
            for template in _entity_templates(entity_templates):
                if "{device_name}" not in template:
                    continue
                domain, object_template = template.split(".", 1)
                suffix = object_template.split("{device_name}", 1)[1]

                for entity_id in entity_ids:
                    entity_domain, entity_object_id = entity_id.split(".", 1)
                    if entity_domain == domain and entity_object_id.endswith(suffix):
                        candidate = entity_object_id[: -len(suffix)]
                        candidates[candidate] = candidates.get(candidate, 0) + 1

    if candidates:
        return max(candidates, key=candidates.get)

    return entity_ids[0].split(".", 1)[1].split("_", 1)[0]


def _entity_template_candidates(templates: str | list[str], device_name: str) -> list[str]:
    return [template.replace("{device_name}", device_name) for template in _entity_templates(templates)]


def _entity_templates(templates: str | list[str]) -> list[str]:
    if isinstance(templates, str):
        return [templates]
    return list(templates)


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
            CONF_INVERTER_LOSS,
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
    callbacks = hass.data[DOMAIN].setdefault("config_callbacks", [])
    for entity_id in hass.data[DOMAIN]["config_entities"].values():
        callback = partial(_state_change_callback, hass)
        callbacks.append(async_track_state_change(hass, entity_id, callback))
    return True


async def _setup_axle_vpp_callbacks(hass: HomeAssistant) -> bool:
    data = hass.data[DOMAIN]
    for unsubscribe in data.pop("axle_vpp_callbacks", []):
        unsubscribe()

    if not hass.config_entries.async_entries(AXLE_VPP_DOMAIN):
        data["axle_vpp_installed"] = False
        _LOGGER.debug("Axle VPP integration is not installed; Miser retains inverter control")
        return True

    data["axle_vpp_installed"] = True
    _LOGGER.info("Axle VPP integration detected; Miser will cede inverter control during Axle events")

    callbacks = []
    for entity_id in AXLE_VPP_START_ENTITIES + AXLE_VPP_END_ENTITIES:
        callbacks.append(async_track_state_change(hass, entity_id, _axle_vpp_state_change_callback(hass)))
    data["axle_vpp_callbacks"] = callbacks
    _schedule_axle_vpp_boundary_callbacks(hass)
    return True


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
        _schedule_axle_vpp_boundary_callbacks(hass)
        await optimise(hass=hass)

    return _callback


def _schedule_axle_vpp_boundary_callbacks(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    for unsubscribe in data.pop("axle_vpp_boundary_callbacks", []):
        unsubscribe()

    window = axle_vpp_control_window(hass)
    if window is None:
        data["axle_vpp_boundary_callbacks"] = []
        return

    callbacks = []
    now = dt_util.utcnow()
    for boundary in ["start", "end"]:
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
    if callbacks:
        _LOGGER.debug(
            "Scheduled %d Axle VPP boundary callbacks for %s - %s",
            len(callbacks),
            window["start"].isoformat(),
            window["end"].isoformat(),
        )


def _axle_vpp_boundary_callback(hass: HomeAssistant, boundary: str):
    async def _callback(_now):
        if hass.data.get(DOMAIN, {}).get("unloading"):
            return

        _LOGGER.info("Axle VPP %s buffer boundary reached; rechecking Miser inverter control", boundary)
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
