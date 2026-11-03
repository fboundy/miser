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

PLATFORMS = ["number", "switch", "sensor"]


UNAVAILABLE_UNKNOWN = ["unavailable", "unknown"]
DEFAULT_BATTERY_VOLTAGE = 50  # V

# Configuration keys
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_BATTERY_CURRENT_LIMIT = "battery_current_limit"
CONF_INVERTER_POWER = "inverter_power"
CONF_CHARGER_POWER = "charger_power"
CONF_INVERTER_EFFICIENCY = "inverter_efficiency"
CONF_CHARGER_EFFICIENCY = "charger_efficiency"
CONF_BATTERY_MINIMUM_SOC = "battery_minimum_soc"
CONF_HISTORY_DAYS = "history_days"
CONF_WEEKDAY_WEIGHTING = "weekday_weighting"
CONF_LOAD_MARGIN = "load_margin"
CONF_DAILY_CONSUMPTION_KWH = "daily_consumption_kwh"
CONF_MINIMUM_DAILY_CONSUMPTION_KWH = "minimum_daily_consumption_kwh"
CONF_OPTIMISER_FREQUENCY = "optimiser_frequency"
CONF_SOLCAST_CONFIDENCE = "solcast_confidence"
CONF_READ_ONLY = "read_only"
CONF_BACKUP_MODE = "backup_mode"
CONF_BACKUP_SOC = "backup_soc"

# Tariff sources offered by the config flow (stored in options["tariff_source"]).
TARIFF_SOURCE_OCTOPUS_ENERGY = "Get tariff codes from Octopus Energy integration"
TARIFF_SOURCE_ACCOUNT = "Specify Octopus Account ID and API Key"
TARIFF_SOURCE_CODES = "Specify Octopus import and export tariff codes directly"
TARIFF_SOURCE_ENTITIES = "Use Home Assistant entities for import and export prices"

# Default values. Optimiser frequency, history days, weekday weighting, load margin, the efficiencies and the
# minimum daily consumption are fixed at these values, not settings (#21).
DEFAULT_CHARGER_POWER = 3000  # W
DEFAULTS = {
    CONF_BATTERY_CAPACITY: 10000,  # Wh
    CONF_BATTERY_CURRENT_LIMIT: DEFAULT_CHARGER_POWER / DEFAULT_BATTERY_VOLTAGE,  # A
    CONF_INVERTER_POWER: 3600,  # W
    CONF_CHARGER_POWER: DEFAULT_CHARGER_POWER,  # W
    CONF_INVERTER_EFFICIENCY: 97,  # Percent
    CONF_CHARGER_EFFICIENCY: 91,  # Percent
    CONF_BATTERY_MINIMUM_SOC: 15,  # %
    CONF_HISTORY_DAYS: 7,
    CONF_WEEKDAY_WEIGHTING: 50,
    CONF_LOAD_MARGIN: 10,
    CONF_DAILY_CONSUMPTION_KWH: 17,
    CONF_MINIMUM_DAILY_CONSUMPTION_KWH: 5,
    CONF_OPTIMISER_FREQUENCY: 10,
    CONF_SOLCAST_CONFIDENCE: 50,
    CONF_READ_ONLY: True,
    # Hold the inverter's Backup SOC (e.g. when power cuts are likely): plan with
    # it as the battery floor and run the inverter in its backup/reserve mode.
    CONF_BACKUP_MODE: False,
    # Reserve held while Backup Mode is on. Miser owns this value: the inverter's
    # own Backup SOC resets to 99% whenever Backup/Reserve is selected.
    CONF_BACKUP_SOC: 50,
}

PV_SYSTEM_ENTITIES = [
    CONF_BATTERY_CAPACITY,
    CONF_INVERTER_POWER,
    CONF_CHARGER_POWER,
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

# Pence thresholds for inverter churn, fixed at the values tuned on the first live install (#18).
WRITE_MINIMISATION_COST = 4  # a plan with fewer inverter writes may cost this much more
WHOLE_HORIZON_WRITE_COST = 5  # Whole Horizon charges this for each control change
ALTERNATION_COST_THRESHOLD = 7  # a plan without rapid charge/discharge reversal may cost this much more
CYCLING_THRESHOLD = 16  # a forced export must be worth this much to stay in the plan

DATETIME_FORMAT_LONG = "%Y-%m-%d %H:%M:%S %z"

IMPORT_EXPORT = ["import", "export"]

OCTOPUS_ACCOUNT_URL = "https://api.octopus.energy/v1/accounts/"
OCTOPUS_PRODUCT_URL = r"https://api.octopus.energy/v1/products/"
OCTOPUS_RATE_URLS = {
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
COST_ENTITY_OBJECTS = "cost_entity_objects"
COST_SLOTS = "slots"
COST_FLOWS = "flows"
CONTROL_STATE = "state"
CONTROL_FORCE_CURRENT = "force_current"
CONTROL_FORCE_POWER = "force_power"
CONTROL_TARGET_SOC = "target_soc"
CONTROL_NEXT_SLOT_START = "next_slot_start"
CONTROL_NEXT_SLOT_END = "next_slot_end"
CONTROL_NEXT_SLOT_POWER = "next_slot_power"
CONTROL_NEXT_SLOT_TARGET_SOC = "next_slot_target_soc"

EMPTY_ATTR: dict[str, Any] = {
    LAST_UPDATED: None,
}

# Switch entities
SWITCH_ENTITIES = {
    conf: {"default": DEFAULTS[conf]}
    for conf in [
        CONF_READ_ONLY,
        CONF_BACKUP_MODE,
    ]
}


# Number entities
NUMBER_ENTITIES = {
    CONF_SOLCAST_CONFIDENCE: {
        "min": 10,
        "max": 90,
        "step": 10,
        "default": DEFAULTS[CONF_SOLCAST_CONFIDENCE],
    },
    # Configurable battery/inverter parameters
    CONF_BATTERY_CAPACITY: {
        "min": 1000,
        "max": 100000,
        "step": 100,
        "default": DEFAULTS[CONF_BATTERY_CAPACITY],
        "unit": UnitOfEnergy.WATT_HOUR,
        "device_class": SensorDeviceClass.ENERGY,
    },
    CONF_INVERTER_POWER: {
        "min": 1000,
        "max": 30000,
        "step": 100,
        "default": DEFAULTS[CONF_INVERTER_POWER],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONF_CHARGER_POWER: {
        "min": 1000,
        "max": 30000,
        "step": 100,
        "default": DEFAULTS[CONF_CHARGER_POWER],
        "unit": UnitOfPower.WATT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONF_DAILY_CONSUMPTION_KWH: {
        "min": 1,
        "max": 30,
        "step": 1,
        "default": DEFAULTS[CONF_DAILY_CONSUMPTION_KWH],
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
    },
    CONF_BACKUP_SOC: {
        "min": 0,
        "max": 100,
        "step": 1,
        "default": DEFAULTS[CONF_BACKUP_SOC],
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
    },
}


# Sensor entities
SENSOR_ENTITIES = {
    "base_cost": {"name": "Cost - Base", "unit": "p", "state_class": SensorStateClass.MEASUREMENT},
    "whole_horizon_cost": {
        "name": "Cost - Whole Horizon",
        "unit": "p",
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "optimised_cost": {"name": "Cost - Optimised", "unit": "p", "state_class": SensorStateClass.MEASUREMENT},
    CONTROL_STATE: {},
    CONTROL_FORCE_CURRENT: {
        "unit": UnitOfElectricCurrent.AMPERE,
        "state_class": SensorStateClass.MEASUREMENT,
        "device_class": SensorDeviceClass.CURRENT,
    },
    CONTROL_FORCE_POWER: {
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONTROL_TARGET_SOC: {
        "unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "device_class": SensorDeviceClass.BATTERY,
    },
    CONTROL_NEXT_SLOT_START: {},
    CONTROL_NEXT_SLOT_END: {},
    CONTROL_NEXT_SLOT_POWER: {
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "device_class": SensorDeviceClass.POWER,
    },
    CONTROL_NEXT_SLOT_TARGET_SOC: {
        "unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "device_class": SensorDeviceClass.BATTERY,
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

    @property
    def extra_state_attributes(self):
        return self._attributes
