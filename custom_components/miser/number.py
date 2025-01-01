import asyncio
import logging
from homeassistant.components.number import NumberEntity

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN, NUMBER_ENTITIES, MiserEntity


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
