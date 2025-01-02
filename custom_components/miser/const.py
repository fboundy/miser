from datetime import timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.entity import DeviceInfo

DOMAIN = "miser"
NAME = "Miser PV System Optimiser"
VERSION = "0.0.1"
MANUFACTURER = "foboundy"

ENTITY_TYPES = ["model_entities", "control_entities"]
PLATFORMS = ["number", "switch"]

OPTIMISER_INTERVAL = timedelta(minutes=10)
OPTIMISER_MAX_ITERS = 3

# Configuration keys
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_BATTERY_CURRENT_LIMIT = "battery_current_limit"
CONF_INVERTER_POWER = "inverter_power"
CONF_CHARGER_POWER = "charger_power"
CONF_INVERTER_EFFICIENCY = "inverter_efficiency"
CONF_CHARGER_EFFICIENCY = "charger_efficiency"
CONF_INVERTER_LOSS = "inverter_loss"

# Default values
DEFAULT_BATTERY_CAPACITY = 10000  # Wh
DEFAULT_BATTERY_CURRENT_LIMIT = 100  # A
DEFAULT_INVERTER_POWER = 3600  # W
DEFAULT_CHARGER_POWER = 3000  # W
DEFAULT_INVERTER_EFFICIENCY = 97  # Percent
DEFAULT_CHARGER_EFFICIENCY = 91  # Percent
DEFAULT_INVERTER_LOSS = 100  # W

DATETIME_FORMAT_LONG = "%Y-%m-%d %H:%M:%S %z"
TIME_FORMAT = "%d/%m %H:%M %Z"

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
    "Weekday weighting": {
        "min": 0,
        "max": 100,
        "step": 10,
        "default": 50,
        "unit": PERCENTAGE,
    },
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
    "Battery current limit": {
        "min": 0,
        "max": 400,
        "step": 10,
        "default": DEFAULT_BATTERY_CURRENT_LIMIT,
        "unit": UnitOfElectricCurrent.AMPERE,
        "device_class": SensorDeviceClass.CURRENT,
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
    "Inverter loss": {
        "min": 0,
        "max": 250,
        "step": 10,
        "default": DEFAULT_INVERTER_LOSS,
        "unit": UnitOfPower.WATT,
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


INVERTER_DEFS = {
    "solis": {
        "solis": {
            "model_entities": {
                "BATTERY_SOC": "sensor.{device_name}_battery_soc",
                "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
                "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_export_today",
                "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
            },
        },
        "solax_modbus": {
            "model_entities": {
                "BATTERY_SOC": "sensor.{device_name}_battery_soc",
                "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
                "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_export_today",
                "CONSUMPTION_TODAY": "sensor.{device_name}_house_load_today",
                "MINIMUM_SOC": "number.{device_name}_battery_minimum_soc",
            },
            "control_entities": {
                "BATTERY_VOLTAGE": "sensor.{device_name}_battery_voltage",
                "TIMED_CHARGE_ON": "switch.{device_name}_timed_charge_slot_1_enable",
                "TIMED_CHARGE_START_HOURS": "number.{device_name}_timed_charge_start_hours",
                "TIMED_CHARGE_START_MINUTES": "number.{device_name}_timed_charge_start_minutes",
                "TIMED_CHARGE_END_HOURS": "number.{device_name}_timed_charge_end_hours",
                "TIMED_CHARGE_END_MINUTES": "number.{device_name}_timed_charge_end_minutes",
                "TIMED_CHARGE_CURRENT": "number.{device_name}_timed_charge_current",
                "TIMED_CHARGE_SOC": "number.{device_name}_timed_charge_soc",
                "TIMED_DISCHARGE_ON": "switch.{device_name}_timed_discharge_slot_1_enable",
                "TIMED_DISCHARGE_START_HOURS": "number.{device_name}_timed_discharge_start_hours",
                "TIMED_DISCHARGE_START_MINUTES": "number.{device_name}_timed_discharge_start_minutes",
                "TIMED_DISCHARGE_END_HOURS": "number.{device_name}_timed_discharge_end_hours",
                "TIMED_DISCHARGE_END_MINUTES": "number.{device_name}_timed_discharge_end_minutes",
                "TIMED_DISCHARGE_CURRENT": "number.{device_name}_timed_discharge_current",
                "TIMED_DISCHARGE_SOC": "number.{device_name}_timed_discharge_soc",
                "TIMED_CHARGE_BUTTON": "button.{device_name}_update_charge_times",
                "TIMED_DISCHARGE_BUTTON": "button.{device_name}_update_discharge_times",
                "TIMED_CHARGE_DISCHARGE_BUTTON": "button.{device_name}_update_charge_discharge_times",
                "INVERTER_MODE": "select.{device_name}_energy_storage_control_switch",
                "BACKUP_MODE_SOC": "number.{device_name}_backup_mode_soc",
            },
        },
        "solisconnect": {
            "model_entities": {
                "BATTERY_SOC": "sensor.{device_name}_battery_soc",
                "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
                "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_export_today",
                "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
            },
        },
    },
}
