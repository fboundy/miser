import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_registry import \
    async_get as async_get_entity_registry

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN, SWITCH_ENTITIES, MiserEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    """
    Set up switch entities from a config entry.
    """
    uuid = None
    while uuid is None:
        uuid = hass.data.get(DOMAIN, {}).get("uuid", None)
        await asyncio.sleep(1)

    entities_to_add = [
        # Create and add the new entity
        OptimiserSwitch(
            config_entry,
            unique_id=f"{DOMAIN}.{uuid}_{name.lower().replace(" ","_")}",
            name=name,
            default_state=entity["default"],
        )
        for name, entity in SWITCH_ENTITIES.items()
    ]

    _LOGGER.debug(f"Switch entities to add: {[entity.unique_id for entity in entities_to_add]}")
    async_add_entities(entities_to_add, update_before_add=True)


class OptimiserSwitch(MiserEntity, SwitchEntity):
    """
    A switch entity for the Miser PV System Optimiser.
    """

    def __init__(
        self,
        config_entry,
        unique_id: str,
        name: str,
        default_state,
    ):
        super().__init__(
            config_entry,
            unique_id,
            name,
        )
        self._attr_is_on = default_state

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._attr_is_on = False
        self.async_write_ha_state()
