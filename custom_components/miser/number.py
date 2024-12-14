from homeassistant.components.number import NumberEntity
import logging


from .const import DOMAIN, NAME, VERSION, NUMBER_ENTITIES, UUID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """
    Set up number entities from a config entry.
    """
    entities = [
        OptimiserNumber(
            config_entry,
            unique_id=f"{DOMAIN}.{UUID}_{id}",
            min_value=entity["min"],
            max_value=entity["max"],
            step=entity["step"],
            default=entity["default"],
            name=f"{DOMAIN}.{id}",
            unit=entity.get("unit", None),
            device_class=entity.get("device_class", None),
        )
        for id, entity in NUMBER_ENTITIES.items()
    ]

    _LOGGER.debug(f"Number entities: {entities}")
    async_add_entities(entities, update_before_add=True)


class OptimiserNumber(NumberEntity):
    """
    A number entity for the Miser PV System Optimiser.
    """

    def __init__(self, config_entry, unique_id, min_value, max_value, step, default, name, unit, device_class):
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_min_value = min_value
        self._attr_max_value = max_value
        self._attr_step = step
        self._attr_value = default
        self._attr_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._config_entry = config_entry

    @property
    def should_poll(self):
        return False
