import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers import entity_registry as er

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
    await asyncio.sleep(1)

    # Add these entities to the config_entities
    entity_registry = er.async_get(hass=hass)

    # Checking the entity registry should handle duplicates better
    for entity in entities_to_add:
        hass.data[DOMAIN]["config_entities"][entity._attr_name] = entity_registry.async_get_entity_id(
            "switch", DOMAIN, entity.unique_id
        )


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

    async def async_added_to_hass(self):
        """Restore the entity state after a restart."""
        # Call the superclass method to ensure proper initialization
        await super().async_added_to_hass()

        # Retrieve the last known state
        last_state = await self.async_get_last_state()
        _LOGGER.debug(f"{self._attr_name}: {last_state}")
        if last_state and last_state.state:
            try:
                # Restore the value from the last state
                self._attr_is_on = last_state.state == "on"
            except ValueError:
                self._attr_is_on = None
