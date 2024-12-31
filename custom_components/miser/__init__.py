import logging
from datetime import datetime, timedelta
from functools import partial
from logging.handlers import RotatingFileHandler

import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .const import DATETIME_FORMAT_LONG, DOMAIN, IMPORT_EXPORT, get_instance_id
from .octopus import (get_octopus_info_from_account,
                      get_octopus_integration_data)

LOG_FILENAME = f"/config/{DOMAIN}.log"

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
    file_handler = RotatingFileHandler(
        LOG_FILENAME, maxBytes=5 * 1024 * 1024, backupCount=3  # 5 MB max size, 3 backups
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

    # Register the device
    # Register the device in the device registry
    # device_registry = dr.async_get(hass)
    # device_registry.async_get_or_create(
    #     config_entry_id=entry.entry_id,
    #     identifiers={(DOMAIN, f"Miser")},
    #     name="Miser",
    #     manufacturer=MANUFACTURER,
    # )

    """
    Check that all the required enities are available for the selected inverter controller
    and intantiate the PV model.

    Save the PV model to hass[DOMAIN].data
    """

    # Load the tariffs
    # Check if we are using the OE integration:
    octopus_info = _get_octopus_info(hass, entry)
    _LOGGER.debug(octopus_info)

    # Set the optimisers to run every 1 minute for testing
    interval = timedelta(minutes=1)

    # Schedule the recurring function with additional logging
    _LOGGER.debug(f"Scheduling _optimise to run every {interval}.")

    try:
        # Pass `hass` explicitly to _optimise by using a partial
        async_track_time_interval(hass, partial(_optimise, hass), interval)
        _LOGGER.debug("async_track_time_interval successfully set up.")
    except Exception as e:
        _LOGGER.error(f"Failed to set up async_track_time_interval: {e}")

    # Forward entries to platform setup
    _LOGGER.debug("Forwarding config entries to platforms: switch, number.")
    hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, "switch"))
    hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, "number"))

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


async def _load_inverter_model(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pass


async def _update_prices(hass: HomeAssistant) -> bool:
    pass
