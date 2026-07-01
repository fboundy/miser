from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from .controller import InverterController
from ..const import (
    CONTROL_BACKUP_MODE_SOC,
    CONTROL_BATTERY_VOLTAGE,
    CONTROL_INVERTER_MODE,
    CONTROL_TIMED_CHARGE_BUTTON,
    CONTROL_TIMED_CHARGE_CURRENT,
    CONTROL_TIMED_CHARGE_END_HOURS,
    CONTROL_TIMED_CHARGE_END_MINUTES,
    CONTROL_TIMED_CHARGE_ON,
    CONTROL_TIMED_CHARGE_SOC,
    CONTROL_TIMED_CHARGE_START_HOURS,
    CONTROL_TIMED_CHARGE_START_MINUTES,
    CONTROL_TIMED_CHARGE_DISCHARGE_BUTTON,
    CONTROL_TIMED_DISCHARGE_BUTTON,
    CONTROL_TIMED_DISCHARGE_CURRENT,
    CONTROL_TIMED_DISCHARGE_END_HOURS,
    CONTROL_TIMED_DISCHARGE_END_MINUTES,
    CONTROL_TIMED_DISCHARGE_ON,
    CONTROL_TIMED_DISCHARGE_SOC,
    CONTROL_TIMED_DISCHARGE_START_HOURS,
    CONTROL_TIMED_DISCHARGE_START_MINUTES,
    ENTITY_TYPES,
    MODEL_BATTERY_MINIMUM_SOC,
    MODEL_BATTERY_SOC,
    MODEL_CONSUMPTION_TODAY,
    MODEL_GRID_EXPORT_TODAY,
    MODEL_GRID_IMPORT_TODAY,
)


class SolisInverter(InverterController):
    """Base class for Solis inverter controller integrations."""

    brand: ClassVar[str] = "solis"
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {}

    def __init__(self, hass, integration_data: dict | None = None) -> None:
        self.hass = hass
        self.integration_data = integration_data or {}

    @property
    @abstractmethod
    def integration(self) -> str:
        """Return the Home Assistant integration domain used by this controller."""

    @property
    def model_entities(self) -> dict[str, str]:
        return self.entity_defs.get("model_entities", {})

    @property
    def control_entities(self) -> dict[str, str]:
        return self.entity_defs.get("control_entities", {})

    @property
    def config_entities(self) -> dict[str, str]:
        return self.entity_defs.get("config_entities", {})

    async def is_online(self) -> bool:
        raise NotImplementedError

    async def get_time(self) -> datetime:
        raise NotImplementedError

    async def set_time(self, time: datetime) -> None:
        raise NotImplementedError

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    async def set_mode(self, mode: str) -> None:
        raise NotImplementedError

    async def get_mode(self) -> str:
        raise NotImplementedError


class SolisSolaxModbusInverter(SolisInverter):
    """Solis inverter controlled through the SolaX Modbus integration."""

    integration: ClassVar[str] = "solax_modbus"
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {
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
    }


class SolisCloudInverter(SolisInverter):
    """Solis inverter controlled through the Solis integration."""

    integration: ClassVar[str] = "solis"
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {
        "model_entities": {
            MODEL_BATTERY_SOC: "sensor.{device_name}_battery_soc",
            MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_grid_import_today",
            MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_grid_export_today",
            MODEL_CONSUMPTION_TODAY: "sensor.{device_name}_consumption_today",
        },
    }


class SolisConnectInverter(SolisInverter):
    """Solis inverter controlled through the SolisConnect integration."""

    integration: ClassVar[str] = "solisconnect"
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {
        "model_entities": {
            MODEL_BATTERY_SOC: "sensor.{device_name}_battery_soc",
            MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_grid_import_today",
            MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_grid_export_today",
            MODEL_CONSUMPTION_TODAY: "sensor.{device_name}_consumption_today",
        },
    }


SOLIS_INVERTER_CLASSES = {
    controller.integration: controller
    for controller in [
        SolisSolaxModbusInverter,
        SolisCloudInverter,
        SolisConnectInverter,
    ]
}


def get_solis_inverter_class(integration: str) -> type[SolisInverter]:
    return SOLIS_INVERTER_CLASSES[integration]


def get_solis_inverter_defs() -> dict[str, dict[str, dict[str, str]]]:
    return {
        integration: {
            entity_type: controller.entity_defs.get(entity_type, {})
            for entity_type in ENTITY_TYPES
        }
        for integration, controller in SOLIS_INVERTER_CLASSES.items()
    }
