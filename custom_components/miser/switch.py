from homeassistant.components.switch import SwitchEntity
import logging

from .const import DOMAIN, NAME, VERSION, SWITCH_ENTITIES, UUID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """
    Set up switch entities from a config entry.
    """
    entities = [
        OptimiserSwitch(
            config_entry, unique_id=f"{DOMAIN}.{UUID}_{id}", name=f"{DOMAIN}.{id}", default_state=entity["default"]
        )
        for id, entity in SWITCH_ENTITIES.items()
    ]
    _LOGGER.debug(f"Switch entities: {entities}")
    async_add_entities(entities, update_before_add=True)


class OptimiserSwitch(SwitchEntity):
    """
    A switch entity for the Miser PV System Optimiser.
    """

    def __init__(self, config_entry, unique_id, default_state, name):
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_is_on = default_state
        self._config_entry = config_entry

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def should_poll(self):
        return False
