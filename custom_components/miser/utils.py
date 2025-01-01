from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


async def get_instance_id(hass: HomeAssistant):
    """
    Example to retrieve Home Assistant instance ID.
    """
    instance_id = await hass.helpers.instance_id.async_get()
    return instance_id[-8:]


async def get_integration_entities(hass: HomeAssistant, integration: str) -> tuple[dict, list]:
    config_entries = hass.config_entries.async_entries(integration)
    if config_entries:
        selected_config_entry = config_entries[0]

        entity_registry = er.async_get(hass=hass)
        associated_entities = [
            entity
            for entity in entity_registry.entities.values()
            if entity.config_entry_id == selected_config_entry.entry_id
        ]

        return selected_config_entry.data, associated_entities
