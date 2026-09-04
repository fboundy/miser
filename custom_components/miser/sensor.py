import asyncio
import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers import entity_registry as er

from .const import (
    COST_ENTITY_OBJECTS,
    COST_FLOWS,
    COST_SLOTS,
    DOMAIN,
    LAST_UPDATED,
    MiserEntity,
    SENSOR_ENTITIES,
    UNAVAILABLE_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up sensor entities from a config entry."""
    uuid = None
    while uuid is None:
        uuid = hass.data.get(DOMAIN, {}).get("uuid", None)
        await asyncio.sleep(1)

    entities_to_add = [
        OptimiserSensor(
            config_entry,
            unique_id=f"{DOMAIN}.{uuid}_{key.lower().replace(' ', '_')}",
            key=key,
            name=entity.get("name", key),
            unit_of_measurement=entity.get("unit", None),
            device_class=entity.get("device_class", None),
            state_class=entity.get("state_class", None),
        )
        for key, entity in SENSOR_ENTITIES.items()
    ]

    _LOGGER.debug(f"Sensor entities to add: {[entity.unique_id for entity in entities_to_add]}")
    async_add_entities(entities_to_add, update_before_add=True)
    await asyncio.sleep(1)

    entity_registry = er.async_get(hass=hass)
    hass.data[DOMAIN][COST_ENTITY_OBJECTS] = {}

    for entity in entities_to_add:
        hass.data[DOMAIN][COST_ENTITY_OBJECTS][entity.key] = entity
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id)
        if entity_id is not None and entity.key.endswith("_cost"):
            entity_registry.async_update_entity(entity_id, name=f"Miser {entity.name}")


class OptimiserSensor(MiserEntity, SensorEntity):
    """A sensor entity for the Miser PV System Optimiser."""

    def __init__(
        self,
        config_entry,
        unique_id,
        key,
        name,
        unit_of_measurement=None,
        icon=None,
        device_class=None,
        state_class=None,
    ):
        super().__init__(
            config_entry,
            unique_id,
            name,
            icon=icon,
            device_class=device_class,
        )
        self.key = key
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = unit_of_measurement
        self._attr_state_class = state_class
        self._attr_extra_state_attributes = self._attributes

    async def async_set_native_value(
        self,
        value: float | str | None,
        slots: list | None = None,
        flows: list | None = None,
        attributes: dict | None = None,
    ) -> None:
        self._attr_native_value = round(value, 1) if isinstance(value, (float, int)) else value
        self._attributes[LAST_UPDATED] = datetime.now().isoformat()
        if slots is not None:
            self._attributes[COST_SLOTS] = slots
        if flows is not None:
            self._attributes[COST_FLOWS] = flows
        if attributes is not None:
            self._attributes.update(attributes)
        self._attr_extra_state_attributes = self._attributes
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Restore the entity state after a restart."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state and last_state.state and last_state.state.lower() not in UNAVAILABLE_UNKNOWN:
            self._attributes.update(last_state.attributes)
            self._attr_extra_state_attributes = self._attributes
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = last_state.state
