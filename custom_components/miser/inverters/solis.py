from abc import abstractmethod
import logging
from datetime import datetime
from typing import Any, ClassVar

import homeassistant.util.dt as dt_util

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
    DOMAIN,
    UNAVAILABLE_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)


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
        return all(
            self._is_available(self._state(entity_id))
            for entity_id in self._entity_ids("model_entities").values()
        )

    async def get_time(self) -> datetime:
        raise NotImplementedError

    async def set_time(self, time: datetime) -> None:
        raise NotImplementedError

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def get_status(self) -> dict[str, Any]:
        status = {}
        for entity_type in ENTITY_TYPES:
            for key, entity_id in self._entity_ids(entity_type).items():
                state = self._state(entity_id)
                status[key] = None if state is None else state.state
        return status

    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        raise NotImplementedError

    async def power_to_current(self, power: float) -> float | None:
        return None

    async def set_mode(self, mode: str) -> None:
        raise NotImplementedError

    async def get_mode(self) -> str:
        raise NotImplementedError

    async def _set_number(self, key: str, value: float) -> None:
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": self._required_entity_id(key),
                "value": round(float(value), 2),
            },
            blocking=True,
        )

    async def _turn_on(self, key: str) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": self._required_entity_id(key)},
            blocking=True,
        )

    async def _turn_off(self, key: str) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": self._required_entity_id(key)},
            blocking=True,
        )

    async def _press(self, key: str) -> None:
        await self.hass.services.async_call(
            "button",
            "press",
            {"entity_id": self._required_entity_id(key)},
            blocking=True,
        )

    def _target_soc(self, target_soc: float) -> int:
        return round(max(0, min(100, float(target_soc))))

    def _numeric_state(self, key: str) -> float | None:
        state = self._state(self._required_entity_id(key))
        if not self._is_available(state):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _required_entity_id(self, key: str) -> str:
        entity_id = self._entity_id(key)
        if entity_id is None:
            raise RuntimeError(f"No {self.integration} entity mapped for {key}")
        return entity_id

    def _entity_id(self, key: str) -> str | None:
        for entity_type in ENTITY_TYPES:
            entity_id = self.hass.data[DOMAIN].get(entity_type, {}).get(key)
            if entity_id is not None:
                return entity_id
        return None

    def _entity_ids(self, entity_type: str) -> dict[str, str]:
        return self.hass.data[DOMAIN].get(entity_type, {})

    def _state(self, entity_id: str | None):
        if entity_id is None:
            return None
        return self.hass.states.get(entity_id)

    def _is_available(self, state) -> bool:
        return state is not None and state.state.lower() not in UNAVAILABLE_UNKNOWN


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
            CONTROL_TIMED_CHARGE_ON: [
                "switch.{device_name}_timed_charge_slot_1_enable",
                "switch.inverter_timed_charge_slot_1_enable",
            ],
            CONTROL_TIMED_CHARGE_START_HOURS: "number.{device_name}_timed_charge_start_hours",
            CONTROL_TIMED_CHARGE_START_MINUTES: "number.{device_name}_timed_charge_start_minutes",
            CONTROL_TIMED_CHARGE_END_HOURS: "number.{device_name}_timed_charge_end_hours",
            CONTROL_TIMED_CHARGE_END_MINUTES: "number.{device_name}_timed_charge_end_minutes",
            CONTROL_TIMED_CHARGE_CURRENT: "number.{device_name}_timed_charge_current",
            CONTROL_TIMED_CHARGE_SOC: "number.{device_name}_timed_charge_soc",
            CONTROL_TIMED_DISCHARGE_ON: [
                "switch.{device_name}_timed_discharge_slot_1_enable",
                "switch.inverter_timed_discharge_slot_1_enable",
            ],
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

    async def get_time(self) -> datetime:
        return dt_util.now()

    async def set_time(self, time: datetime) -> None:
        _LOGGER.debug("SolaX Modbus time control is not exposed; ignoring set_time(%s)", time)

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_time_window(
            start=start,
            end=end,
            start_hours_key=CONTROL_TIMED_CHARGE_START_HOURS,
            start_minutes_key=CONTROL_TIMED_CHARGE_START_MINUTES,
            end_hours_key=CONTROL_TIMED_CHARGE_END_HOURS,
            end_minutes_key=CONTROL_TIMED_CHARGE_END_MINUTES,
        )
        await self._set_number(CONTROL_TIMED_CHARGE_SOC, self._target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_CHARGE_CURRENT, await self._power_to_current(power))
        if self._entity_id(CONTROL_TIMED_CHARGE_ON) is not None:
            await self._turn_on(CONTROL_TIMED_CHARGE_ON)
        await self._press(CONTROL_TIMED_CHARGE_BUTTON)

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_time_window(
            start=start,
            end=end,
            start_hours_key=CONTROL_TIMED_DISCHARGE_START_HOURS,
            start_minutes_key=CONTROL_TIMED_DISCHARGE_START_MINUTES,
            end_hours_key=CONTROL_TIMED_DISCHARGE_END_HOURS,
            end_minutes_key=CONTROL_TIMED_DISCHARGE_END_MINUTES,
        )
        await self._set_number(CONTROL_TIMED_DISCHARGE_SOC, self._target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_DISCHARGE_CURRENT, await self._power_to_current(power))
        if self._entity_id(CONTROL_TIMED_DISCHARGE_ON) is not None:
            await self._turn_on(CONTROL_TIMED_DISCHARGE_ON)
        await self._press(CONTROL_TIMED_DISCHARGE_BUTTON)

    async def control_idle(self) -> None:
        if self._entity_id(CONTROL_TIMED_CHARGE_ON) is not None:
            await self._turn_off(CONTROL_TIMED_CHARGE_ON)
        if self._entity_id(CONTROL_TIMED_DISCHARGE_ON) is not None:
            await self._turn_off(CONTROL_TIMED_DISCHARGE_ON)
        if self._entity_id(CONTROL_TIMED_CHARGE_DISCHARGE_BUTTON) is not None:
            await self._press(CONTROL_TIMED_CHARGE_DISCHARGE_BUTTON)
        else:
            await self._press(CONTROL_TIMED_CHARGE_BUTTON)
            await self._press(CONTROL_TIMED_DISCHARGE_BUTTON)

    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        if state == "idle":
            return (
                self._entity_id(CONTROL_TIMED_CHARGE_ON) is None
                or not self._switch_on(CONTROL_TIMED_CHARGE_ON)
            ) and (
                self._entity_id(CONTROL_TIMED_DISCHARGE_ON) is None
                or not self._switch_on(CONTROL_TIMED_DISCHARGE_ON)
            )

        if state == "charging":
            enable_key = CONTROL_TIMED_CHARGE_ON
            current_key = CONTROL_TIMED_CHARGE_CURRENT
            soc_key = CONTROL_TIMED_CHARGE_SOC
        elif state == "discharging":
            enable_key = CONTROL_TIMED_DISCHARGE_ON
            current_key = CONTROL_TIMED_DISCHARGE_CURRENT
            soc_key = CONTROL_TIMED_DISCHARGE_SOC
        else:
            return False

        requested_current = await self._power_to_current(power)
        actual_current = self._numeric_state(current_key)
        actual_soc = self._numeric_state(soc_key)

        controls_match = (
            (self._entity_id(enable_key) is None or self._switch_on(enable_key))
            and actual_current is not None
            and actual_soc is not None
            and abs(actual_current - requested_current) <= 0.1
            and (target_soc is None or abs(actual_soc - self._target_soc(target_soc)) <= 1)
        )
        if controls_match:
            _LOGGER.debug("Refreshing %s control despite matching Solax entity states", state)
        return False

    async def set_mode(self, mode: str) -> None:
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self._required_entity_id(CONTROL_INVERTER_MODE),
                "option": mode,
            },
            blocking=True,
        )

    async def get_mode(self) -> str:
        state = self._state(self._required_entity_id(CONTROL_INVERTER_MODE))
        return None if state is None else state.state

    async def _set_time_window(
        self,
        start: datetime,
        end: datetime,
        start_hours_key: str,
        start_minutes_key: str,
        end_hours_key: str,
        end_minutes_key: str,
    ) -> None:
        start = dt_util.as_local(start)
        end = dt_util.as_local(end)
        await self._set_number(start_hours_key, start.hour)
        await self._set_number(start_minutes_key, start.minute)
        await self._set_number(end_hours_key, end.hour)
        await self._set_number(end_minutes_key, end.minute)

    async def _set_current(self, key: str, current: float) -> None:
        actual_current = self._numeric_state(key)
        if actual_current is None or abs(actual_current - current) < 0.1:
            reference_current = current if actual_current is None else actual_current
            nudge = reference_current - 0.1 if reference_current >= 0.1 else reference_current + 0.1
            await self._set_number(key, nudge)

        await self._set_number(key, current)

    async def _power_to_current(self, power: float) -> float:
        current = await self.power_to_current(power)
        if current is None:
            raise RuntimeError("Battery voltage is unavailable; cannot convert power to Solax current")
        return current

    async def power_to_current(self, power: float) -> float | None:
        voltage = self._numeric_state(CONTROL_BATTERY_VOLTAGE)
        if voltage is None or voltage <= 0:
            return None
        return abs(float(power)) / voltage

    def _switch_on(self, key: str) -> bool:
        entity_id = self._entity_id(key)
        if entity_id is None:
            return False
        state = self._state(entity_id)
        return state is not None and state.state.lower() == "on"


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
