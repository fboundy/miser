from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.config_entries import ConfigEntry
import logging

from .const import DOMAIN, NAME, VERSION, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Miser PV System Optimiser from a config entry.
    """
    # Register the device
    # Register the device in the device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "miser_pv_system_optimiser")},
        name=NAME,
        manufacturer=MANUFACTURER,
    )

    # Forward entries to platform setup
    hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, "switch"))
    hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, "number"))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry.
    """
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "switch")
    unload_ok &= await hass.config_entries.async_forward_entry_unload(entry, "number")
    return unload_ok
