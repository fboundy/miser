import asyncio
import logging

from datetime import datetime

from homeassistant.components.number import NumberEntity
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN, NUMBER_ENTITIES, MiserEntity, LAST_UPDATED


async def async_setup_entry(hass, config_entry, async_add_entities):
    """
    Set up number entities from a config entry.
    """
    uuid = None
    while uuid is None:
        uuid = hass.data.get(DOMAIN, {}).get("uuid", None)
        await asyncio.sleep(1)

    entities_to_add = [
        OptimiserNumber(
            config_entry,
            unique_id=f"{DOMAIN}.{uuid}_{name.lower().replace(" ","_")}",
            name=name,
            min_value=entity["min"],
            max_value=entity["max"],
            step=entity["step"],
            default=entity["default"],
            unit_of_measurement=entity.get("unit", None),
            device_class=entity.get("device_class", None),
        )
        for name, entity in NUMBER_ENTITIES.items()
    ]

    _LOGGER.debug(f"Number entities to add: {[entity.unique_id for entity in entities_to_add]}")
    async_add_entities(entities_to_add, update_before_add=True)
    await asyncio.sleep(1)

    # Add these entities to the config_entities
    entity_registry = er.async_get(hass=hass)

    for entity in entities_to_add:
        hass.data[DOMAIN]["config_entities"][entity.name.replace(" ", "_").upper()] = (
            entity_registry.async_get_entity_id("number", DOMAIN, entity.unique_id)
        )


class OptimiserNumber(MiserEntity, NumberEntity):
    """
    A number entity for the Miser PV System Optimiser.
    """

    def __init__(
        self,
        config_entry,
        unique_id,
        name,
        min_value,
        max_value,
        step,
        default,
        unit_of_measurement=None,
        icon=None,
        device_class=None,
    ):
        super().__init__(
            config_entry,
            unique_id,
            name,
            icon=icon,
            device_class=device_class,
        )
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_value = default
        self._attr_unit_of_measurement = unit_of_measurement

    async def async_set_native_value(self, value: float) -> None:
        _LOGGER.debug(f"async_set_native_value for {self._attr_name}")
        self._attr_native_value = value
        self._attributes[LAST_UPDATED] = datetime.now()
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Restore the entity state after a restart."""
        # Call the superclass method to ensure proper initialization
        await super().async_added_to_hass()

        # Retrieve the last known state
        last_state = await self.async_get_last_state()

        if last_state and last_state.state:
            try:
                # Restore the value from the last state
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = None
