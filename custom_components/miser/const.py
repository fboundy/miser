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
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import DeviceInfo
from typing import Any

DOMAIN = "miser"
NAME = "Miser PV System Optimiser"
VERSION = "0.0.1"
MANUFACTURER = "foboundy"

NULL_STATES = ["Unavailable", "Unknown"]
SWITCH_STATES = ["On", "on", "Off", "off"]

ENTITY_TYPES = [
    "model_entities",  # Typically sensors that report a value used by the model
    "control_entities",  # Entities used to control the inverter
    "config_entities",  # Entities used to configure the model/optimiser
]

PLATFORMS = ["number", "switch"]

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
DEFAULTS = {
    "BATTERY_CAPACITY": 10000,  # Wh
    "BATTERY_CURRENT_LIMIT": 100,  # A
    "INVERTER_POWER": 3600,  # W
    "CHARGER_POWER": 3000,  # W
    "INVERTER_EFFICIENCY": 97,  # Percent
    "CHARGER_EFFICIENCY": 91,  # Percent
    "INVERTER_LOSS": 100,  # W
    "BATTERY_MINIMUM_SOC": 15,  # %
    "USE_CONSUMPTION": True,
    "HISTORY_DAYS": 7,
    "WEEKDAY_WEIGHTING": 50,
    "LOAD_MARGIN": 10,
    "SHAPE_CONSUMPTION": True,
    "DAILY_CONSUMPTION_KWH": 17,
    "OPTIMISER_FREQUENCY": 10,
}

PV_SYSTEM_ENTITIES = [
    "BATTERY_CAPACITY",
    "BATTERY_CURRENT_LIMIT",
    "INVERTER_POWER",
    "CHARGER_POWER",
    "INVERTER_EFFICIENCY",
    "CHARGER_EFFICIENCY",
    "INVERTER_LOSS",
    "BATTERY_MINIMUM_SOC",
]

MODEL_DURATION_HOURS = 48
MODEL_PERIOD_MINUTES = 30
MODEL_COLUMNS = [
    "dt_hours",
    "solar",
    "consumption",
    "battery",
    "grid",
    "charge_start",
    "charge_end",
    "SOC_start",
    "SOC_end",
    "import_price",
    "export_price",
    "net_cost",
]

DATETIME_FORMAT_LONG = "%Y-%m-%d %H:%M:%S %z"
TIME_FORMAT = "%d/%m %H:%M %Z"

IMPORT_EXPORT = ["import", "export"]

OCTOPUS_ACCOUNT_URL = "https://api.octopus.energy/v1/accounts/"
OCTOPUS_PRODUCT_URL = r"https://api.octopus.energy/v1/products/"
OCTOPUS_RATE_URLS = {
    "fixed": OCTOPUS_PRODUCT_URL + "{product}/electricity-tariffs/{code}/standing-charges/",
    "day": OCTOPUS_PRODUCT_URL + "{product}/electricity-tariffs/{code}/day-unit-rates/",
    "night": OCTOPUS_PRODUCT_URL + "{product}/electricity-tariffs/{code}/night-unit-rates/",
    "unit": OCTOPUS_PRODUCT_URL + "{product}/electricity-tariffs/{code}/standard-unit-rates/",
}

OCTOPUS_PRODUCT_PARAMS = {
    "page_size": 500,
    "order_by": "period",
}

AGILE_PREDICT_URL = r"https://agilepredict.com/api/"

SOLCAST_INTEGRATION = "solcast_solar"
SOLCAST_PV_KEY = "pv_forecast_forecast"
SOLCAST_DETAILED_FORECAST_ATTRIBUTE = "detailedForecast"
SOLCAST_FORECAST_PERIODS = ["today", "tomorrow"] + [f"day_{i}" for i in range(2, 8)]
SOLCAST_COLUMNS = ["pv_estimate", "pv_estimate10", "pv_estimate90"]

CONSUMPTION_SHAPE = [
    {"hours": 00.00, "consumption": 300},
    {"hours": 00.50, "consumption": 200},
    {"hours": 06.00, "consumption": 150},
    {"hours": 08.00, "consumption": 500},
    {"hours": 15.50, "consumption": 500},
    {"hours": 17.00, "consumption": 750},
    {"hours": 22.00, "consumption": 750},
    {"hours": 24.00, "consumption": 300},
]

# ATTRIBUTES
LAST_UPDATED = "Last updated"

EMPTY_ATTR: dict[str, Any] = {
    LAST_UPDATED: None,
}

# Switch entities
SWITCH_ENTITIES = {
    "Read only": {"default": False},
    "Include export": {"default": False},
    "Optimise discharging": {"default": False},
    "Use solar": {"default": True},
    "Use consumption history": {"default": DEFAULTS["USE_CONSUMPTION"]},
    "Shape consumption": {"default": DEFAULTS["SHAPE_CONSUMPTION"]},
}


# Number entities
NUMBER_ENTITIES = {
    "Optimiser frequency": {
        "min": 5,
        "max": 30,
        "step": 5,
        "default": DEFAULTS["OPTIMISER_FREQUENCY"],
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
        "default": DEFAULTS["HISTORY_DAYS"],
    },
    "Load margin": {
        "min": 0,
        "max": 25,
        "step": 5,
        "default": DEFAULTS["LOAD_MARGIN"],
        "unit": PERCENTAGE,
    },
    "Weekday weighting": {
        "min": 0,
        "max": 100,
        "step": 10,
        "default": DEFAULTS["WEEKDAY_WEIGHTING"],
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
        "default": DEFAULTS["BATTERY_CAPACITY"],
        "unit": UnitOfEnergy.WATT_HOUR,
        "device_class": SensorDeviceClass.ENERGY,
    },
    "Battery current limit": {
        "min": 0,
        "max": 400,
        "step": 10,
        "default": DEFAULTS["BATTERY_CURRENT_LIMIT"],
        "unit": UnitOfElectricCurrent.AMPERE,
        "device_class": SensorDeviceClass.CURRENT,
    },
    "Inverter power": {
        "min": 1000,
        "max": 10000,
        "step": 100,
        "default": DEFAULTS["INVERTER_POWER"],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "Charger power": {
        "min": 1000,
        "max": 5000,
        "step": 100,
        "default": DEFAULTS["CHARGER_POWER"],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    "Inverter efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULTS["INVERTER_EFFICIENCY"],
        "unit": PERCENTAGE,
    },
    "Charger efficiency": {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULTS["CHARGER_EFFICIENCY"],
        "unit": PERCENTAGE,
    },
    "Inverter loss": {
        "min": 0,
        "max": 250,
        "step": 10,
        "default": DEFAULTS["INVERTER_LOSS"],
        "unit": UnitOfPower.WATT,
    },
    "Daily consumption": {
        "min": 0,
        "max": 30,
        "step": 1,
        "default": DEFAULTS["DAILY_CONSUMPTION_KWH"],
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
    },
}


class MiserEntity(RestoreEntity):
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
        self._attributes = dict(EMPTY_ATTR)

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
                "BATTERY_MINIMUM_SOC": "number.{device_name}_battery_minimum_soc",
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
