import logging
import asyncio
from datetime import timedelta
from functools import partial
from logging.handlers import RotatingFileHandler

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change

from .const import (
    PV_SYSTEM_ENTITIES,
    DOMAIN,
    IMPORT_EXPORT,
    INVERTER_DEFS,
    ENTITY_TYPES,
    PLATFORMS,
    CONF_BATTERY_CAPACITY,
    MODEL_BATTERY_MINIMUM_SOC,
    CONF_BATTERY_CURRENT_LIMIT,
    CONF_INVERTER_LOSS,
    CONF_CHARGER_EFFICIENCY,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_POWER,
    CONF_OPTIMISER_FREQUENCY,
    MODEL_ENTITIES_AVAILABLE_WAIT,
)
from .octopus import get_octopus_info_from_account, get_octopus_integration_data, Tariff
from .utils import get_instance_id, get_integration_entities, get_value, get_key_for_entity, log_config_entry
from .pv_model import InverterModel, BatteryModel, PVsystemModel
from .optimiser import optimise

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
    asyncio.sleep(1)

    # Test the logging setup
    _LOGGER.debug("Custom logging initialized.")

    uuid = await get_instance_id(hass)
    _LOGGER.debug(f"UUID: {uuid}")
    hass.data[DOMAIN] = {"uuid": uuid}

    log_config_entry(entry)

    for entity_type in ENTITY_TYPES:
        hass.data[DOMAIN][entity_type] = {}

    # Forward entries to platform setup
    _LOGGER.debug("Forwarding config entries to platforms: switch, number.")
    tasks = [
        hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, platform))
        for platform in PLATFORMS
    ]

    await asyncio.gather(*tasks, return_exceptions=True)

    """
    Check that all the required entities are available for the selected inverter controller
    and intantiate the PV model.

    Save the PV model to hass.data[DOMAIN]
    """
    entities_available = await _get_entities(hass, entry)

    if not entities_available:
        _LOGGER.error("Could not retrieve necessary entities to set up inverter model")
        octopus = False

    else:
        _log_all_entities(hass)
        pv_model = await _load_pv_system_model(hass)

        # Load the tariffs
        # Check if we are using the OE integration:
        octopus = await _get_octopus_info(hass, entry)

    while not (pv_model and octopus):
        asyncio.sleep(1)
        _LOGGER.debug(f"Waiting for PV model and tariff info.")

    _LOGGER.debug(hass.data[DOMAIN].get("octopus_info"))

    # Set up the schedule for the optimise
    await _schedule_optimiser(hass)

    # Set up callbacks for when the config entities change
    await _setup_config_callbacks(hass)

    return True


async def _get_octopus_info(hass: HomeAssistant, entry: ConfigEntry):
    if entry.options.get("tariff_source", "") == "Get tariff codes from Octopus Energy integration":
        _LOGGER.debug("Loading data from OE integration")
        octopus_info = await get_octopus_integration_data(hass)
    elif entry.options.get("tariff_source", "") == "Specify Octopus Account ID and API Key":
        _LOGGER.debug("Loading Octopus data from API using account details")
        octopus_info = await get_octopus_info_from_account(
            hass, entry.data.get("account_id", None), entry.data.get("api_key", None)
        )
    elif entry.options.get("tariff_source", "") == "Specify Octopus import and export tariff codes directly":
        octopus_info = {
            "tariff_code": {direction: entry.options.get(f"{direction}_code", None) for direction in IMPORT_EXPORT}
        }
    else:
        octopus_info = {}
        _LOGGER.debug(f"No octopus data found. Tariff source: {entry.options.get('tariff_source', '')}")

    hass.data[DOMAIN]["octopus_info"] = octopus_info
    hass.data[DOMAIN]["tariffs"] = {}
    for direction, tariff_code in octopus_info.get("tariff_code", {}).items():
        hass.data[DOMAIN]["tariffs"][direction] = await Tariff.create(tariff_code, export=(direction == "export"))

    return len(octopus_info) > 0


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.
    """
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "switch")
    unload_ok &= await hass.config_entries.async_forward_entry_unload(entry, "number")
    return unload_ok


async def _get_entities(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Saves required entities to hass.data[DOMAIN]["entities"] from the entity register
    """
    brand = entry.data["inverter_brand"]
    integration = entry.options["integration"]

    _LOGGER.debug(f"Checking entities for inverter brand {brand} with integration {integration}")
    integration_data, integration_entities = await get_integration_entities(hass=hass, integration=integration)
    integration_device_name = integration_entities[0].entity_id.split(".")[1].split("_")[0]
    _LOGGER.debug(f"Integration device name: {integration_device_name}")

    hass.data[DOMAIN]["integration_data"] = integration_data
    index_lookup = {entity.entity_id: i for i, entity in enumerate(integration_entities)}

    success = True

    for entity_type in ENTITY_TYPES:
        entity_ids = INVERTER_DEFS[brand][integration].get(entity_type, [])
        for key in entity_ids:
            expected_entity_id = entity_ids[key].replace("{device_name}", integration_device_name)
            str_log = f"  {key:35s}: {expected_entity_id:50s} "
            index = index_lookup.get(expected_entity_id, None)
            if index is None:
                str_log += "Not found"

                # We may not need all the control entities so only fail if the main entities aren't all there
                if entity_type == "model_entities":
                    success = False

            else:
                str_log += f"index: {index:4d}"
                entity_id = integration_entities[index].entity_id
                hass.data[DOMAIN][entity_type][key] = entity_id

            # _LOGGER.debug(str_log)

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
            CONF_INVERTER_LOSS,
        ]
    }

    # Load the inverter model
    hass.data[DOMAIN]["inverter_model"] = InverterModel(**kw_inverter)

    # Read the battery model parameters from the relevant config entities
    kw_battery = {
        kw: await get_value(hass, kw)
        for kw in [CONF_BATTERY_CAPACITY, CONF_BATTERY_CURRENT_LIMIT, MODEL_BATTERY_MINIMUM_SOC]
    }

    # Load the battery model
    hass.data[DOMAIN]["battery_model"] = BatteryModel(**kw_battery)

    # Load the PV system model
    hass.data[DOMAIN]["model"] = PVsystemModel(
        inverter=hass.data[DOMAIN]["inverter_model"], battery=hass.data[DOMAIN]["battery_model"]
    )

    return True


async def _schedule_optimiser(hass):
    optimiser_minutes = await get_value(hass=hass, key=CONF_OPTIMISER_FREQUENCY, default_value=10)
    _LOGGER.debug(f"Optimiser frequency: {optimiser_minutes} minutes")
    optimiser_interval = timedelta(minutes=optimiser_minutes)

    try:
        # Cancel the current schedule if it exists
        if DOMAIN in hass.data and "optimiser_schedule" in hass.data[DOMAIN]:
            hass.data[DOMAIN]["optimiser_schedule"]()
            _LOGGER.debug("Previous schedule cancelled.")

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
            await optimise(hass)
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
        hass.loop.call_later(initial_delay, lambda: hass.async_create_task(initial_run()))

    except Exception as e:
        _LOGGER.error(f"Failed to set up _schedule_optimiser: {e}")


async def _setup_config_callbacks(hass):
    for entity_id in hass.data[DOMAIN]["config_entities"].values():
        callback = partial(_state_change_callback, hass)
        async_track_state_change(hass, entity_id, callback)
    return True


async def _state_change_callback(hass, entity_id, old_state, new_state):
    """Callback function triggered when the config entity state changes."""
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
