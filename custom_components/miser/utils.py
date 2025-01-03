import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from numpy import nan

from .const import DOMAIN, NULL_STATES, SWITCH_STATES, ENTITY_TYPES, DEFAULTS


_LOGGER = logging.getLogger(__name__)


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


async def get_value(hass: HomeAssistant, key: str, default_value=None) -> bool | int | float:
    """
    Gets the value of a variable by looking up the entity_id for variable name in the DOMAIN data
    """

    _LOGGER.debug(f"Getting value for {key}:")
    entity_id = get_entity_for_key(hass, key)

    _LOGGER.debug(f"  Entity ID: {entity_id}")
    if entity_id is not None:
        state = hass.states.get(entity_id)
        _LOGGER.debug(f"  State: {state}")
    else:
        state = None
        _LOGGER.debug(f"  Entity ID: UNAVAILABLE! <===")

    if state is not None:
        value = state_to_value(state.state)
        _LOGGER.debug(f"  Value: {value}")
    else:
        if default_value is not None:
            value = default_value
            _LOGGER.debug(f"  Value: {value} [Explicit Default]")
        elif key in DEFAULTS:
            value = DEFAULTS[key]
            _LOGGER.debug(f"  Value: {value} [System Default]")
    _LOGGER.debug(f"  Type: {type(value)}")
    return value


def state_to_value(state):
    if state in NULL_STATES:
        return nan
    elif state in SWITCH_STATES:
        return state in ["on", "On"]
    else:
        try:
            return float(state)
        except:
            return state


def get_key_for_entity(hass: HomeAssistant, entity_id: str) -> str:
    for entities in [hass.data[DOMAIN][entity_type] for entity_type in ENTITY_TYPES]:
        keys = {entities[key]: key for key in entities}
        key = keys.get(entity_id)
        if key is not None:
            break
    return key


def get_entity_for_key(hass: HomeAssistant, key: str) -> str:
    for entities in [hass.data[DOMAIN][entity_type] for entity_type in ENTITY_TYPES]:
        entity_id = entities.get(key, None)
        if entity_id is not None:
            break
    return entity_id
