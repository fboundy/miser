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
OPTIMISER_HIGH_COST_MAX_ITERS = 200

UNAVAILABLE_UNKNOWN = ["unavailable", "unknown"]

# Configuration keys
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_BATTERY_CURRENT_LIMIT = "battery_current_limit"
CONF_INVERTER_POWER = "inverter_power"
CONF_CHARGER_POWER = "charger_power"
CONF_INVERTER_EFFICIENCY = "inverter_efficiency"
CONF_CHARGER_EFFICIENCY = "charger_efficiency"
CONF_INVERTER_LOSS = "inverter_loss"
CONF_BATTERY_MINIMUM_SOC = "battery_minimum_soc"
CONF_USE_CONSUMPTION_HISTORY = "use_consumption_history"
CONF_HISTORY_DAYS = "history_days"
CONF_WEEKDAY_WEIGHTING = "weekday_weighting"
CONF_LOAD_MARGIN = "load_margin"
CONF_SHAPE_CONSUMPTION = "shape_consumption"
CONF_DAILY_CONSUMPTION_KWH = "daily_consumption_kwh"
CONF_OPTIMISER_FREQUENCY = "optimiser_frequency"
CONF_SOLCAST_CONFIDENCE = "solcast_confidence"
CONF_POWER_RESOLUTION = "power_resolution"
CONF_SLEEP_SOC = "sleep_soc"
CONF_USE_SOLAR = "use_solar"
CONF_READ_ONLY = "read_only"
CONF_INCLUDE_EXPORT = "include_export"
CONF_OPTIMISE_DISCHARGING = "optimise_discharging"

# Default values
DEFAULTS = {
    CONF_BATTERY_CAPACITY: 10000,  # Wh
    CONF_BATTERY_CURRENT_LIMIT: 100,  # A
    CONF_INVERTER_POWER: 3600,  # W
    CONF_CHARGER_POWER: 3000,  # W
    CONF_INVERTER_EFFICIENCY: 97,  # Percent
    CONF_CHARGER_EFFICIENCY: 91,  # Percent
    CONF_INVERTER_LOSS: 100,  # W
    CONF_BATTERY_MINIMUM_SOC: 15,  # %
    CONF_USE_CONSUMPTION_HISTORY: True,
    CONF_HISTORY_DAYS: 7,
    CONF_WEEKDAY_WEIGHTING: 50,
    CONF_LOAD_MARGIN: 10,
    CONF_SHAPE_CONSUMPTION: True,
    CONF_DAILY_CONSUMPTION_KWH: 17,
    CONF_OPTIMISER_FREQUENCY: 10,
    CONF_SOLCAST_CONFIDENCE: 50,
    CONF_POWER_RESOLUTION: 100,
    CONF_SLEEP_SOC: 0,
    CONF_USE_SOLAR: True,
    CONF_READ_ONLY: True,
    CONF_INCLUDE_EXPORT: True,
    CONF_OPTIMISE_DISCHARGING: True,
}

PV_SYSTEM_ENTITIES = [
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CURRENT_LIMIT,
    CONF_INVERTER_POWER,
    CONF_CHARGER_POWER,
    CONF_INVERTER_EFFICIENCY,
    CONF_CHARGER_EFFICIENCY,
    CONF_INVERTER_LOSS,
    CONF_BATTERY_MINIMUM_SOC,
]

MODEL_BATTERY_SOC = "battery_soc"
MODEL_GRID_IMPORT_TODAY = "grid_import_today"
MODEL_GRID_EXPORT_TODAY = "grid_export_today"
MODEL_CONSUMPTION_TODAY = "house_load_today"
MODEL_BATTERY_MINIMUM_SOC = "battery_minimum_soc"
MODEL_DURATION_HOURS = 48
MODEL_PERIOD_MINUTES = 30
MODEL_ENTITIES_AVAILABLE_WAIT = 60
MODEL_MIN_SLOT_POWER = 10  # W
MODEL_MAX_SEARCH_WINDOW_SOC = 97  # %

CONTROL_BATTERY_VOLTAGE = "battery_voltage"
CONTROL_TIMED_CHARGE_ON = "timed_charge_enable"
CONTROL_TIMED_CHARGE_START_HOURS = "timed_charge_start_hours"
CONTROL_TIMED_CHARGE_START_MINUTES = "timed_charge_start_minutes"
CONTROL_TIMED_CHARGE_END_HOURS = "timed_charge_end_hours"
CONTROL_TIMED_CHARGE_END_MINUTES = "timed_charge_end_minutes"
CONTROL_TIMED_CHARGE_CURRENT = "timed_charge_current"
CONTROL_TIMED_CHARGE_SOC = "timed_charge_soc"
CONTROL_TIMED_DISCHARGE_ON = "timed_discharge_slot_enable"
CONTROL_TIMED_DISCHARGE_START_HOURS = "timed_discharge_start_hours"
CONTROL_TIMED_DISCHARGE_START_MINUTES = "timed_discharge_start_minutes"
CONTROL_TIMED_DISCHARGE_END_HOURS = "timed_discharge_end_hours"
CONTROL_TIMED_DISCHARGE_END_MINUTES = "timed_discharge_end_minutes"
CONTROL_TIMED_DISCHARGE_CURRENT = "timed_discharge_current"
CONTROL_TIMED_DISCHARGE_SOC = "timed_discharge_soc"
CONTROL_TIMED_CHARGE_BUTTON = "update_charge_times"
CONTROL_TIMED_DISCHARGE_BUTTON = "update_discharge_times"
CONTROL_TIMED_CHARGE_DISCHARGE_BUTTON = "update_charge_discharge_times"
CONTROL_INVERTER_MODE = "energy_storage_control_switch"
CONTROL_BACKUP_MODE_SOC = "backup_mode_soc"
CONTROL_PASS_THREHOLD = 4
CONTROL_SLOT_THRESHOLD = 1

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
    conf: {"default": DEFAULTS[conf]}
    for conf in [
        CONF_USE_CONSUMPTION_HISTORY,
        CONF_SHAPE_CONSUMPTION,
        CONF_USE_SOLAR,
        CONF_READ_ONLY,
        CONF_INCLUDE_EXPORT,
        CONF_OPTIMISE_DISCHARGING,
    ]
}


# Number entities
NUMBER_ENTITIES = {
    CONF_OPTIMISER_FREQUENCY: {
        "min": 5,
        "max": 30,
        "step": 5,
        "default": DEFAULTS[CONF_OPTIMISER_FREQUENCY],
    },
    CONF_SOLCAST_CONFIDENCE: {
        "min": 10,
        "max": 90,
        "step": 10,
        "default": DEFAULTS[CONF_SOLCAST_CONFIDENCE],
    },
    CONF_HISTORY_DAYS: {
        "min": 1,
        "max": 14,
        "step": 1,
        "default": DEFAULTS[CONF_HISTORY_DAYS],
    },
    CONF_LOAD_MARGIN: {
        "min": 0,
        "max": 25,
        "step": 5,
        "default": DEFAULTS[CONF_LOAD_MARGIN],
        "unit": PERCENTAGE,
    },
    CONF_WEEKDAY_WEIGHTING: {
        "min": 0,
        "max": 100,
        "step": 10,
        "default": DEFAULTS[CONF_WEEKDAY_WEIGHTING],
        "unit": PERCENTAGE,
    },
    CONF_POWER_RESOLUTION: {
        "min": 0,
        "max": 500,
        "step": 100,
        "default": DEFAULTS[CONF_POWER_RESOLUTION],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONF_SLEEP_SOC: {
        "min": 0,
        "max": 20,
        "step": 1,
        "default": DEFAULTS[CONF_SLEEP_SOC],
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
    },
    # Configurable battery/inverter parameters
    CONF_BATTERY_CAPACITY: {
        "min": 1000,
        "max": 20000,
        "step": 100,
        "default": DEFAULTS[CONF_BATTERY_CAPACITY],
        "unit": UnitOfEnergy.WATT_HOUR,
        "device_class": SensorDeviceClass.ENERGY,
    },
    CONF_BATTERY_CURRENT_LIMIT: {
        "min": 0,
        "max": 400,
        "step": 10,
        "default": DEFAULTS[CONF_BATTERY_CURRENT_LIMIT],
        "unit": UnitOfElectricCurrent.AMPERE,
        "device_class": SensorDeviceClass.CURRENT,
    },
    CONF_INVERTER_POWER: {
        "min": 1000,
        "max": 10000,
        "step": 100,
        "default": DEFAULTS[CONF_INVERTER_POWER],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONF_CHARGER_POWER: {
        "min": 1000,
        "max": 5000,
        "step": 100,
        "default": DEFAULTS[CONF_CHARGER_POWER],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONF_INVERTER_EFFICIENCY: {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULTS[CONF_INVERTER_EFFICIENCY],
        "unit": PERCENTAGE,
    },
    CONF_CHARGER_EFFICIENCY: {
        "min": 50,
        "max": 100,
        "step": 1,
        "default": DEFAULTS[CONF_CHARGER_EFFICIENCY],
        "unit": PERCENTAGE,
    },
    CONF_INVERTER_LOSS: {
        "min": 0,
        "max": 250,
        "step": 10,
        "default": DEFAULTS[CONF_INVERTER_LOSS],
        "unit": UnitOfPower.WATT,
    },
    CONF_DAILY_CONSUMPTION_KWH: {
        "min": 0,
        "max": 30,
        "step": 1,
        "default": DEFAULTS[CONF_DAILY_CONSUMPTION_KWH],
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

    @property
    def name(self):
        return self._attr_name.replace("_", " ").title()


INVERTER_DEFS = {
    "solis": {
        "solax_modbus": {
            "model_entities": {
                MODEL_BATTERY_SOC: "sensor.{device_name}_battery_soc",
                MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_grid_import_today",
                MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_grid_export_today",
                MODEL_CONSUMPTION_TODAY: "sensor.{device_name}_house_load_today",
                MODEL_BATTERY_MINIMUM_SOC: "number.{device_name}_battery_minimum_soc",
            },
            "control_entities": {
                CONTROL_BATTERY_VOLTAGE: "sensor.{device_name}_battery_voltage",
                CONTROL_TIMED_CHARGE_ON: "switch.{device_name}_timed_charge_slot_1_enable",
                CONTROL_TIMED_CHARGE_START_HOURS: "number.{device_name}_timed_charge_start_hours",
                CONTROL_TIMED_CHARGE_START_MINUTES: "number.{device_name}_timed_charge_start_minutes",
                CONTROL_TIMED_CHARGE_END_HOURS: "number.{device_name}_timed_charge_end_hours",
                CONTROL_TIMED_CHARGE_END_MINUTES: "number.{device_name}_timed_charge_end_minutes",
                CONTROL_TIMED_CHARGE_CURRENT: "number.{device_name}_timed_charge_current",
                CONTROL_TIMED_CHARGE_SOC: "number.{device_name}_timed_charge_soc",
                CONTROL_TIMED_DISCHARGE_ON: "switch.{device_name}_timed_discharge_slot_1_enable",
                CONTROL_TIMED_DISCHARGE_START_HOURS: "number.{device_name}_timed_discharge_start_hours",
                CONTROL_TIMED_DISCHARGE_START_MINUTES: "number.{device_name}_timed_discharge_start_minutes",
                CONTROL_TIMED_DISCHARGE_END_HOURS: "number.{device_name}_timed_discharge_end_hours",
                CONTROL_TIMED_DISCHARGE_END_MINUTES: "number.{device_name}_timed_discharge_end_minutes",
                CONTROL_TIMED_DISCHARGE_CURRENT: "number.{device_name}_timed_discharge_current",
                CONTROL_TIMED_DISCHARGE_SOC: "number.{device_name}_timed_discharge_soc",
                CONTROL_TIMED_CHARGE_BUTTON: "button.{device_name}_update_charge_times",
                CONTROL_TIMED_DISCHARGE_BUTTON: "button.{device_name}_update_discharge_times",
                CONTROL_TIMED_CHARGE_DISCHARGE_BUTTON: "button.{device_name}_update_charge_discharge_times",
                CONTROL_INVERTER_MODE: "select.{device_name}_energy_storage_control_switch",
                CONTROL_BACKUP_MODE_SOC: "number.{device_name}_backup_mode_soc",
            },
        },
        "solis": {
            "model_entities": {
                "BATTERY_SOC": "sensor.{device_name}_battery_soc",
                "GRID_IMPORT_TODAY": "sensor.{device_name}_grid_import_today",
                "GRID_EXPORT_TODAY": "sensor.{device_name}_grid_export_today",
                "CONSUMPTION_TODAY": "sensor.{device_name}_consumption_today",
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
