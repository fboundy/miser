from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (PERCENTAGE, UnitOfApparentPower,
                                 UnitOfElectricCurrent,
                                 UnitOfElectricPotential, UnitOfEnergy,
                                 UnitOfFrequency, UnitOfPower,
                                 UnitOfReactivePower, UnitOfTemperature,
                                 UnitOfTime)
from homeassistant.helpers.entity import DeviceInfo

# from homeassistant.helpers.instance_id import async_get


async def get_instance_id(hass):
    """
    Example to retrieve Home Assistant instance ID.
    """
    instance_id = await hass.helpers.instance_id.async_get()
    return instance_id[-8:]


DOMAIN = "miser"
NAME = "Miser PV System Optimiser"
VERSION = "1.0.0"
MANUFACTURER = "foboundy"
# Configuration keys
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_INVERTER_POWER = "inverter_power"
CONF_CHARGER_POWER = "charger_power"
CONF_INVERTER_EFFICIENCY = "inverter_efficiency"
CONF_CHARGER_EFFICIENCY = "charger_efficiency"

# Default values
DEFAULT_BATTERY_CAPACITY = 10000  # Wh
DEFAULT_INVERTER_POWER = 3600  # W
DEFAULT_CHARGER_POWER = 3000  # W
DEFAULT_INVERTER_EFFICIENCY = 97  # Percent
DEFAULT_CHARGER_EFFICIENCY = 91  # Percent

DATETIME_FORMAT_LONG = "%Y-%m-%d %H:%M:%S %z"

IMPORT_EXPORT = ["import", "export"]

OCTOPUS_ACCOUNT_URL = "https://api.octopus.energy/v1/accounts/"

# Switch entities
SWITCH_ENTITIES = {
    "Read only": {"default": False},
    "Include export": {"default": False},
    "Optimise discharging": {"default": False},
    "Use solar": {"default": True},
    "Use consumption history": {"default": True},
}


# Number entities
NUMBER_ENTITIES = {
    "Optimiser frequency": {
        "min": 5,
        "max": 30,
        "step": 5,
        "default": 10,
    },
    "Solcast confidence": {
        "min": 10,
        "max": 90,
        "step": 10,
        "default": 50,
    },
    "History days": {
        "min": 1,
        "max": 14,
        "step": 1,
        "default": 7,
    },
    "Load margin": {
        "min": 0,
        "max": 25,
        "step": 5,
        "default": 10,
        "unit": PERCENTAGE,
    },
    "Weekday weighting": {"min": 0, "max": 100, "step": 10, "default": 50, "unit": PERCENTAGE},
    "Power resolution": {
        "min": 0,
        "max": 500,
        "step": 100,
        "default": 100,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "Sleep SOC": {
        "min": 0,
        "max": 20,
        "step": 1,
        "default": 0,
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
    },
    # Configurable battery/inverter parameters
    "Battery capacity": {
        "min": 1000,
        "max": 20000,
        "step": 100,
        "default": DEFAULT_BATTERY_CAPACITY,
        "unit": UnitOfEnergy.WATT_HOUR,
        "device_class": SensorDeviceClass.ENERGY,
    },
    "Inverter power": {
        "min": 1000,
        "max": 10000,
        "step": 100,
        "default": DEFAULT_INVERTER_POWER,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "Charger power": {
        "min": 1000,
        "max": 5000,
        "step": 100,
        "default": DEFAULT_CHARGER_POWER,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "Inverter efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULT_INVERTER_EFFICIENCY,
        "unit": PERCENTAGE,
    },
    "Charger efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULT_CHARGER_EFFICIENCY,
        "unit": PERCENTAGE,
    },
}


class MiserEntity:
    def __init__(
        self,
        config_entry,
        unique_id: str,
        name: str,
        icon=None,
        device_class=None,
    ) -> None:
        self.unique_id = unique_id
        self.has_entity_name = True
        self._attr_name = name
        self._attr_device_class = device_class
        self._config_entry = config_entry
        self._icon = icon

    @property
    def should_poll(self):
        return False

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return a device description for device registry."""
        return DeviceInfo(
            identifiers={(DOMAIN, NAME)},
            name="Miser",
            manufacturer=MANUFACTURER,
            entry_type="service",
        )


CONFIG = {
    "solis": {
        "solis": {
            "BATTERY_SOC": "sensor.{device_name}_battery_soc",
            "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
        },
        "solax_modbus": {
            "BATTERY_SOC": "sensor.{device_name}_battery_soc",
            "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
        },
        "solisconnect": {
            "BATTERY_SOC": "sensor.{device_name}_battery_soc",
            "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_import_today",
            "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
        },
    },
}
