from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import IMPORT_EXPORT, OCTOPUS_ACCOUNT_URL

import pandas as pd
import aiohttp
import logging
from datetime import datetime


_LOGGER = logging.getLogger(__name__)


async def fetch_api_data(url: str, params: dict = None, api_key: str = None) -> dict:
    """
    Perform an asynchronous HTTP GET request.

    Args:
        url (str): The API endpoint URL.
        params (dict): Optional query parameters for the request.
        api_key (str): Optional API key for authentication.

    Returns:
        dict: The parsed JSON response from the API.

    Raises:
        Exception: If the request fails or the response cannot be parsed.
    """
    auth = aiohttp.BasicAuth(api_key, "") if api_key else None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, auth=auth) as response:
                _LOGGER.debug(f"Request URL: {response.url}")
                response.raise_for_status()  # Raise an error for HTTP errors
                data = await response.json()  # Parse JSON response
                _LOGGER.debug(f"Response Data: {data}")
                return data
    except Exception as e:
        _LOGGER.error(f"Error fetching data from {url}: {e}")
        raise


async def get_octopus_integration_data(hass: HomeAssistant) -> dict:
    """
    Interrogate the Octopus Energy intergration and extract as much data as possible
    """

    config_entries = hass.config_entries.async_entries("octopus_energy")

    # Log ConfigEntry contents
    if config_entries:
        _LOGGER.debug("Octopus Energy integration:")
        entry = config_entries[0]
        _LOGGER.debug(f"  ConfigEntry data: {entry.data}")
        _LOGGER.debug(f"  ConfigEntry options: {entry.options}")
        _LOGGER.debug(f"  ConfigEntry unique ID: {entry.unique_id}")
        _LOGGER.debug(f"  ConfigEntry title: {entry.title}")

        config_keys = ["account_id", "api_key"]
        octopus_info = {key: entry.data.get(key, None) for key in config_keys}

        entity_registry = er.async_get(hass=hass)
        octopus_entities = [
            entity for entity in entity_registry.entities.values() if entity.config_entry_id == entry.entry_id
        ]

        current_day_entities = [entity for entity in octopus_entities if "current_day_rates" in entity.entity_id]
        entity_keys = ["mpan", "serial_number", "tariff_code"]
        for key in entity_keys:
            octopus_info[key] = {direction: {} for direction in IMPORT_EXPORT}

            for entity in current_day_entities:
                state = hass.states.get(entity.entity_id)
                if "xport" in state.attributes.get("friendly_name", ""):
                    direction = "export"
                else:
                    direction = "import"
                octopus_info[key][direction] = state.attributes.get(key, None)
            if all([octopus_info[key][direction] is None for direction in IMPORT_EXPORT]):
                octopus_info[key] = None

        all_keys = config_keys + entity_keys

        if any([octopus_info[key] is None for key in all_keys]):
            _LOGGER.debug("  Incomplete Octopus data")
            return None
        else:
            _LOGGER.debug(f"  Octopus Data: {octopus_info}")
            return octopus_info

    else:
        _LOGGER.debug("  No config entries found")


async def get_octopus_info_from_account(hass: HomeAssistant, account_id: str, api_key: str) -> dict:
    _LOGGER.debug(f"account: {account_id}  api_key: {api_key}")
    # if any([account_id is None, api_key is None]):
    #     _LOGGER.error("Unable to get Octopus account data")
    #     return None

    octopus_info = {"account_id": account_id, "api_key": api_key}

    url = f"{OCTOPUS_ACCOUNT_URL}/{account_id}/"
    _LOGGER.debug(f"Connecting to {url}")

    data = await fetch_api_data(url=url, api_key=api_key)
    _LOGGER.debug(data)

    mpans = data["properties"][0]["electricity_meter_points"]
    # for mpan in mpans:
    #     redact_patterns.append(mpan["mpan"])

    entity_keys = ["mpan", "serial_number", "tariff_code"]
    octopus_info = octopus_info | {key: {direction: {} for direction in IMPORT_EXPORT} for key in entity_keys}
    for mpan_data in mpans:
        df = pd.DataFrame(mpan_data["agreements"])
        df = df.set_index("valid_from")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        tariff_code = df["tariff_code"].iloc[-1]

        _LOGGER.debug(f"Retrieved most recent tariff code {tariff_code}")
        if mpan_data["is_export"]:
            direction = "export"
        else:
            direction = "import"

        octopus_info["mpan"][direction] = mpan_data["mpan"]
        octopus_info["tariff_code"][direction] = tariff_code
        octopus_info["serial_number"][direction] = mpan_data["meters"][0]["serial_number"]

    return octopus_info


async def get_octopus_prices_from_api(hass: HomeAssistant, start: datetime, end: datetime) -> pd.DataFrame:
    pass


async def get_agile_predict(hass: HomeAssistant, start: datetime, end: datetime) -> pd.Series:
    pass
