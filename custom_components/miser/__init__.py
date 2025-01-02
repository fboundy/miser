import logging
import asyncio
from datetime import datetime
from functools import partial
from logging.handlers import RotatingFileHandler

import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DATETIME_FORMAT_LONG,
    DOMAIN,
    IMPORT_EXPORT,
    INVERTER_DEFS,
    OPTIMISER_INTERVAL,
    ENTITY_TYPES,
    PLATFORMS,
)
from .octopus import get_octopus_info_from_account, get_octopus_integration_data
from .utils import get_instance_id, get_integration_entities, get_config
from .pv_model import InverterModel, BatteryModel, PVsystemModel

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
        log_filename, maxBytes=5 * 1024 * 1024, backupCount=3  # 5 MB max size, 3 backups
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(module)-15s - %(levelname)-8s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
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

    uuid = await get_instance_id(hass)
    _LOGGER.debug(f"UUID: {uuid}")
    hass.data[DOMAIN] = {"uuid": uuid}

    # Log ConfigEntry contents
    _LOGGER.debug(f"ConfigEntry data: {entry.data}")
    _LOGGER.debug(f"ConfigEntry options: {entry.options}")
    _LOGGER.debug(f"ConfigEntry unique ID: {entry.unique_id}")
    _LOGGER.debug(f"ConfigEntry title: {entry.title}")

    for entity_type in ENTITY_TYPES:
        hass.data[DOMAIN][entity_type] = {}

    try:
        # Pass `hass` explicitly to _optimise by using a partial
        async_track_time_interval(hass, partial(_optimise, hass), OPTIMISER_INTERVAL)
        _LOGGER.debug("async_track_time_interval successfully set up.")
    except Exception as e:
        _LOGGER.error(f"Failed to set up async_track_time_interval: {e}")

    # Forward entries to platform setup
    _LOGGER.debug("Forwarding config entries to platforms: switch, number.")
    tasks = [
        hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, platform))
        for platform in PLATFORMS
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    """
    Check that all the required entities are available for the selected inverter controller
    and intantiate the PV model.

    Save the PV model to hass.data[DOMAIN]
    """
    entities_available = await _get_entities(hass, entry)
    if not entities_available:
        _LOGGER.error("Could not retrieve necessary entities to set up inverter model")
    else:
        for entity_type in ENTITY_TYPES:
            _LOGGER.debug(f"{entity_type}:")
            _LOGGER.debug(f"{'-'*(len(entity_type)+1)}")
            for entity in hass.data[DOMAIN][entity_type]:
                _LOGGER.debug(
                    f"{entity:35s}:{hass.data[DOMAIN][entity_type][entity].domain} {hass.data[DOMAIN][entity_type][entity].platform} {hass.data[DOMAIN][entity_type][entity].unique_id}"
                )
            _LOGGER.debug("")
        await _load_pv_system_model(hass, entry)

    # Load the tariffs
    # Check if we are using the OE integration:
    octopus_info = _get_octopus_info(hass, entry)
    _LOGGER.debug(octopus_info)

    # Schedule the recurring function with additional logging
    _LOGGER.debug(f"Scheduling _optimise to run every {OPTIMISER_INTERVAL}.")

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

    return octopus_info


async def _optimise(hass: HomeAssistant, now: datetime):
    try:
        # Access hass.data
        uuid = hass.data[DOMAIN]["uuid"]
        _LOGGER.debug(f"optiMISER executed at: {now.strftime(DATETIME_FORMAT_LONG)}. UUID: {uuid}")
    except Exception as e:
        _LOGGER.error(f"Error in _optimise function: {e}")


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
        entity_ids = INVERTER_DEFS[brand][integration][entity_type]
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
                hass.data[DOMAIN][entity_type][key] = integration_entities[index]

            _LOGGER.debug(str_log)

    return success


async def _load_pv_system_model(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Read the inverter model parameters from the relevant config entities
    kw_inverter = {
        kw: get_config(hass, kw)
        for kw in ["inverter_efficiency", "charger_efficiency", "inverter_loss", "inverter_power", "charger_power"]
    }

    # Load the inverter model
    hass.data[DOMAIN]["inverter_model"] = InverterModel(**kw_inverter)

    # Read the battery model parameters from the relevant config entities
    kw_battery = {kw: get_config(hass, kw) for kw in ["capacity", "max_dod", "current_limit_amps"]}

    # Load the battery model
    hass.data[DOMAIN]["battery_model"] = BatteryModel(**kw_battery)

    # Load the PV system model
    hass.data[DOMAIN]["battery_model"] = PVsystemModel(
        inverter=hass.data[DOMAIN]["inverter_model"], battery=hass.data[DOMAIN]["battery_model"]
    )


async def _update_prices(hass: HomeAssistant) -> bool:
    pass
