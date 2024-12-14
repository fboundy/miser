from homeassistant.components.sensor import (
    SensorStateClass,
    SensorDeviceClass,
)

from homeassistant.const import (
    UnitOfPower,
    UnitOfApparentPower,
    UnitOfEnergy,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfFrequency,
    UnitOfReactivePower,
    UnitOfTime,
    PERCENTAGE,
)

from uuid import uuid4

DOMAIN = "miser"
NAME = "Miser PV System Optimiser"
VERSION = "1.0.0"
MANUFACTURER = "Your Company"
UUID = uuid4().__str__()[-12:]

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

# Switch entities
SWITCH_ENTITIES = {
    "read_only": {"default": False},
    "include_export": {"default": False},
    "optimise_discharging": {"default": False},
    "use_solar": {"default": True},
    "use_consumption_history": {"default": True},
}

# Number entities
NUMBER_ENTITIES = {
    "optimiser_frequency": {
        "min": 5,
        "max": 30,
        "step": 5,
        "default": 10,
    },
    "solcast_confidence": {
        "min": 10,
        "max": 90,
        "step": 10,
        "default": 50,
    },
    "history_days": {
        "min": 1,
        "max": 14,
        "step": 1,
        "default": 7,
    },
    "load_margin": {
        "min": 0,
        "max": 25,
        "step": 5,
        "default": 10,
        "unit": PERCENTAGE,
    },
    "weekday_weighting": {"min": 0, "max": 100, "step": 10, "default": 50, "unit": PERCENTAGE},
    "power_resolution": {
        "min": 0,
        "max": 500,
        "step": 100,
        "default": 100,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "sleep_soc": {
        "min": 0,
        "max": 20,
        "step": 1,
        "default": 0,
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
    },
    # Configurable battery/inverter parameters
    "battery_capacity": {
        "min": 1000,
        "max": 20000,
        "step": 100,
        "default": DEFAULT_BATTERY_CAPACITY,
        "unit": UnitOfEnergy.WATT_HOUR,
        "device_class": SensorDeviceClass.ENERGY,
    },
    "inverter_power": {
        "min": 1000,
        "max": 10000,
        "step": 100,
        "default": DEFAULT_INVERTER_POWER,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "charger_power": {
        "min": 1000,
        "max": 5000,
        "step": 100,
        "default": DEFAULT_CHARGER_POWER,
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "inverter_efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULT_INVERTER_EFFICIENCY,
        "unit": PERCENTAGE,
    },
    "charger_efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULT_CHARGER_EFFICIENCY,
        "unit": PERCENTAGE,
    },
}
