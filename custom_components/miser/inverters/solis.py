from abc import abstractmethod
import asyncio
import logging
from datetime import datetime
from typing import ClassVar

import homeassistant.util.dt as dt_util

from .controller import InverterController

SOLIS_NOMINAL_BATTERY_VOLTAGE = 48  # V
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

SOLIS_CLOUD_TIMED_MODE = "Self-Use Mode - Allow Grid Charging"
SOLIS_CLOUD_IDLE_MODE = "Self-Use Mode - No Grid Charging"
SOLIS_CONNECT_IDLE_MODE = "Self-Use"
SOLIS_CONNECT_BACKUP_MODE = "Backup/Reserve"
SOLIS_CONNECT_STORAGE_CONTROL = "storage_control_value"
# Work-mode option for each value of the storage-control register (43110), as
# defined by SolisConnect's work-mode select.
SOLIS_CONNECT_MODES_BY_REGISTER = {
    1: "Self-Use - No Grid Charging",
    17: "Backup/Reserve - No Grid Charging",
    33: "Self-Use",
    37: "Off-Grid Mode",
    41: "Battery Awaken",
    49: "Backup/Reserve",
    64: "Feed-in priority - No Grid Charging",
    96: "Feed-in priority",
}
# SolisConnect sets its control entities on write, before the inverter takes the
# value. These read-only register sensors show what the inverter actually holds.
SOLIS_CONNECT_TOU_SWITCH = "tou_v2_switch"
# Register 43707 bits for slot 1.
SOLIS_CONNECT_TOU_SWITCH_BITS = {CONTROL_TIMED_CHARGE_ON: 0, CONTROL_TIMED_DISCHARGE_ON: 6}
SOLIS_CONNECT_TOU_TIMES = {
    CONTROL_TIMED_CHARGE_START_HOURS: "charge_start",
    CONTROL_TIMED_CHARGE_END_HOURS: "charge_end",
    CONTROL_TIMED_DISCHARGE_START_HOURS: "discharge_start",
    CONTROL_TIMED_DISCHARGE_END_HOURS: "discharge_end",
}

# How long to wait after writing before reading the inverter back. The Solis
# integrations update their control entities optimistically on write, so a
# read taken sooner than the next register poll only echoes what we sent.
# SolisConnect polls the timed-charge block every 30 s.
CONTROL_CONFIRM_DELAY_SECONDS = 45
CONTROL_CONFIRM_CURRENT_TOLERANCE = 0.2  # A
CONTROL_CONFIRM_SOC_TOLERANCE = 1  # %


class SolisInverter(InverterController):
    """Base class for Solis inverter controller integrations."""

    brand: ClassVar[str] = "solis"
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {}
    # Inverter mode that a timed charge/discharge requires, or None when the
    # controller does not set one.
    control_mode: ClassVar[str | None] = None
    # Mode used instead of control_mode while Miser's Backup Mode is on: the
    # inverter holds the battery at its backup SOC and switches the backup port
    # to the battery the moment the grid drops. None if not supported.
    backup_control_mode: ClassVar[str | None] = None

    def __init__(self, hass, integration_data: dict | None = None) -> None:
        self.hass = hass
        self.integration_data = integration_data or {}
        # Set by the optimiser on every run from Miser's Backup Mode switch and
        # Backup SOC number.
        self.backup_mode = False
        self.backup_soc: float | None = None

    async def ensure_backup_soc(self) -> None:
        """Keep the inverter's Backup SOC at Miser's value while Backup Mode is on.

        Selecting Backup/Reserve resets the inverter's Backup SOC to 99%, so this
        runs after any mode change and rewrites the reserve only when it differs.
        """
        if not self.backup_mode or self.backup_soc is None:
            return
        if self._entity_id(CONTROL_BACKUP_MODE_SOC) is None:
            return
        target = self._target_soc(self.backup_soc)
        current = self._numeric_state(CONTROL_BACKUP_MODE_SOC)
        if current is not None and abs(current - target) < 1:
            return
        await self._set_number(CONTROL_BACKUP_MODE_SOC, target)

    @property
    def supports_backup_mode(self) -> bool:
        return self.backup_control_mode is not None

    @property
    def active_control_mode(self) -> str | None:
        """The base inverter mode Miser should hold right now."""
        if self.backup_mode and self.backup_control_mode is not None:
            return self.backup_control_mode
        return self.control_mode

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

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        raise NotImplementedError

    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        raise NotImplementedError

    async def power_to_current(self, power: float) -> float | None:
        voltage = self._numeric_state(CONTROL_BATTERY_VOLTAGE)
        if voltage is None or voltage <= 0:
            return None
        current = abs(float(power)) / voltage
        # Solis specs the battery current at 48 V nominal (RHI-3.6K-48ES-5G: 3 kW, 62.5 A) and reverts any
        # write above it, so below 48 V the full power can't be reached.
        # shortcut: assumes the charger power setting is the datasheet battery power; read the model's limit if one is exposed.
        inverter_model = self.hass.data[DOMAIN].get("inverter_model")
        if inverter_model is not None and inverter_model.charger_power:
            current = min(current, inverter_model.charger_power / SOLIS_NOMINAL_BATTERY_VOLTAGE)
        return current

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

    async def get_mode(self) -> str | None:
        state = self._state(self._required_entity_id(CONTROL_INVERTER_MODE))
        return None if state is None else state.state

    async def confirm_control(
        self,
        state: str,
        start: datetime,
        end: datetime,
        target_soc: float,
        power: float,
    ) -> list[str]:
        await asyncio.sleep(CONTROL_CONFIRM_DELAY_SECONDS)
        return await self._control_mismatches(state, start, end, target_soc, power)

    async def _control_mismatches(
        self,
        state: str,
        start: datetime,
        end: datetime,
        target_soc: float,
        power: float,
    ) -> list[str]:
        """Compare the requested control with what the integration now reports."""
        if state == "charging":
            enable_key = CONTROL_TIMED_CHARGE_ON
            opposite_key = CONTROL_TIMED_DISCHARGE_ON
            start_keys = (CONTROL_TIMED_CHARGE_START_HOURS, CONTROL_TIMED_CHARGE_START_MINUTES)
            end_keys = (CONTROL_TIMED_CHARGE_END_HOURS, CONTROL_TIMED_CHARGE_END_MINUTES)
            current_key = CONTROL_TIMED_CHARGE_CURRENT
            soc_key = CONTROL_TIMED_CHARGE_SOC
        elif state == "discharging":
            enable_key = CONTROL_TIMED_DISCHARGE_ON
            opposite_key = CONTROL_TIMED_CHARGE_ON
            start_keys = (CONTROL_TIMED_DISCHARGE_START_HOURS, CONTROL_TIMED_DISCHARGE_START_MINUTES)
            end_keys = (CONTROL_TIMED_DISCHARGE_END_HOURS, CONTROL_TIMED_DISCHARGE_END_MINUTES)
            current_key = CONTROL_TIMED_DISCHARGE_CURRENT
            soc_key = CONTROL_TIMED_DISCHARGE_SOC
        else:
            return [f"unsupported control state {state!r}"]

        mismatches = []

        expected_mode = self.active_control_mode
        if expected_mode is not None:
            mode = await self.get_mode()
            if mode != expected_mode:
                mismatches.append(f"mode is {mode!r}, expected {expected_mode!r}")

        if self._entity_id(enable_key) is not None and not self._slot_enabled(enable_key):
            mismatches.append(f"{state} enable switch is off")
        if self._slot_enabled(opposite_key):
            mismatches.append("the opposite timed slot is still enabled")

        for label, keys, requested in (("start", start_keys, start), ("end", end_keys, end)):
            if self._entity_id(keys[0]) is None:
                continue
            requested_local = dt_util.as_local(requested)
            actual = self._programmed_time(*keys)
            if actual != (requested_local.hour, requested_local.minute):
                actual_text = "unavailable" if actual is None else f"{actual[0]:02d}:{actual[1]:02d}"
                mismatches.append(f"{label} is {actual_text}, expected {requested_local.strftime('%H:%M')}")

        actual_soc = self._numeric_state(soc_key)
        expected_soc = self._programmed_target_soc(state, target_soc)
        if actual_soc is None or abs(actual_soc - expected_soc) > CONTROL_CONFIRM_SOC_TOLERANCE:
            mismatches.append(f"target SOC is {actual_soc}, expected {expected_soc}")

        requested_current = await self._power_to_current(power)
        actual_current = self._numeric_state(current_key)
        if actual_current is None or abs(actual_current - requested_current) > CONTROL_CONFIRM_CURRENT_TOLERANCE:
            mismatches.append(f"current is {actual_current}A, expected {requested_current:.1f}A")

        return mismatches

    def _programmed_time(self, hours_key: str, minutes_key: str) -> tuple[int, int] | None:
        """Read back a programmed time as (hour, minute).

        Integrations expose either a single `time.*` entity under the hours key
        or separate hour and minute `number.*` entities.
        """
        entity_id = self._entity_id(hours_key)
        state = self._state(entity_id)
        if not self._is_available(state):
            return None

        if entity_id.startswith("time."):
            parsed = dt_util.parse_time(state.state)
            if parsed is None:
                return None
            return parsed.hour, parsed.minute

        minutes = self._numeric_state(minutes_key) if self._entity_id(minutes_key) is not None else 0
        try:
            hours = float(state.state)
        except (TypeError, ValueError):
            return None
        if minutes is None:
            return None
        return int(hours), int(minutes)

    # Writes are blocking so an integration that fails to reach the inverter
    # raises here instead of silently dropping the command.
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

    async def _set_time(self, key: str, value: datetime) -> None:
        value = dt_util.as_local(value)
        await self.hass.services.async_call(
            "time",
            "set_value",
            {
                "entity_id": self._required_entity_id(key),
                "time": value.strftime("%H:%M:%S"),
            },
            blocking=True,
        )

    async def _set_current(self, key: str, current: float) -> None:
        actual_current = self._numeric_state(key)
        _LOGGER.debug(
            "Setting %s current to %.2fA; current entity state is %s",
            key,
            current,
            f"{actual_current:.2f}A" if actual_current is not None else "unavailable",
        )

        if actual_current is not None and abs(actual_current - current) < 0.1:
            _LOGGER.debug("%s current already matches requested value; skipping write", key)
            return

        await self._set_number(key, current)

    async def _power_to_current(self, power: float) -> float:
        current = await self.power_to_current(power)
        if current is None:
            raise RuntimeError("Battery voltage is unavailable; cannot convert power to Solis current")
        return current

    def _target_soc(self, target_soc: float) -> int:
        return round(max(0, min(100, float(target_soc))))

    def _discharge_target_soc(self, target_soc: float) -> int:
        """Discharge target the inverter will accept.

        A Solis rejects a timed-discharge target at or below its over-discharge
        SOC and silently keeps the previous target (writing 15 with a 15% floor
        left the slot at 54%, so a discharge would stop far too early). Raise
        the target to one above that floor when the floor is known.
        """
        target = self._target_soc(target_soc)
        if self._entity_id(MODEL_BATTERY_MINIMUM_SOC) is None:
            return target
        floor = self._numeric_state(MODEL_BATTERY_MINIMUM_SOC)
        if floor is None:
            return target
        return max(target, self._target_soc(floor + 1))

    def _programmed_target_soc(self, state: str, target_soc: float) -> int:
        """The target SOC actually written for this control state."""
        if state == "discharging":
            return self._discharge_target_soc(target_soc)
        return self._target_soc(target_soc)

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

    def _switch_on(self, key: str) -> bool:
        entity_id = self._entity_id(key)
        if entity_id is None:
            return False
        state = self._state(entity_id)
        return state is not None and state.state.lower() == "on"

    def _slot_enabled(self, key: str) -> bool:
        """Whether the inverter holds a timed slot enabled, for confirming writes."""
        return self._switch_on(key)

    async def _turn_off_if_on(self, key: str) -> None:
        """Switch off the opposite timed slot so charge and discharge never overlap."""
        if self._entity_id(key) is not None and self._switch_on(key):
            await self._turn_off(key)


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
        await self._turn_off_if_on(CONTROL_TIMED_DISCHARGE_ON)
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
        await self._set_number(CONTROL_TIMED_DISCHARGE_SOC, self._discharge_target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_DISCHARGE_CURRENT, await self._power_to_current(power))
        await self._turn_off_if_on(CONTROL_TIMED_CHARGE_ON)
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
        """The optimiser only asks about "idle"."""
        if state != "idle":
            return False
        return (
            self._entity_id(CONTROL_TIMED_CHARGE_ON) is None or not self._switch_on(CONTROL_TIMED_CHARGE_ON)
        ) and (
            self._entity_id(CONTROL_TIMED_DISCHARGE_ON) is None or not self._switch_on(CONTROL_TIMED_DISCHARGE_ON)
        )

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

class SolisCloudInverter(SolisInverter):
    """Solis inverter controlled through the Solis integration."""

    integration: ClassVar[str] = "solis"
    control_mode: ClassVar[str | None] = SOLIS_CLOUD_TIMED_MODE
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {
        "model_entities": {
            MODEL_BATTERY_SOC: "sensor.{device_name}_solis_remaining_battery_capacity",
            MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_solis_daily_grid_energy_purchased",
            MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_solis_daily_on_grid_energy",
            MODEL_CONSUMPTION_TODAY: "sensor.{device_name}_solis_daily_grid_energy_used",
        },
        "control_entities": {
            CONTROL_BATTERY_VOLTAGE: "sensor.{device_name}_solis_battery_voltage",
            CONTROL_TIMED_CHARGE_START_HOURS: "time.{device_name}_solis_timed_charge_start_1",
            CONTROL_TIMED_CHARGE_END_HOURS: "time.{device_name}_solis_timed_charge_end_1",
            CONTROL_TIMED_CHARGE_CURRENT: "number.{device_name}_solis_timed_charge_current_1",
            CONTROL_TIMED_CHARGE_SOC: "number.{device_name}_solis_timed_charge_soc_1",
            CONTROL_TIMED_DISCHARGE_START_HOURS: "time.{device_name}_solis_timed_discharge_start_1",
            CONTROL_TIMED_DISCHARGE_END_HOURS: "time.{device_name}_solis_timed_discharge_end_1",
            CONTROL_TIMED_DISCHARGE_CURRENT: "number.{device_name}_solis_timed_discharge_current_1",
            CONTROL_TIMED_DISCHARGE_SOC: "number.{device_name}_solis_timed_discharge_soc_1",
            CONTROL_TIMED_CHARGE_BUTTON: "button.{device_name}_solis_update_timed_charge_1",
            CONTROL_TIMED_DISCHARGE_BUTTON: "button.{device_name}_solis_update_timed_discharge_1",
            CONTROL_INVERTER_MODE: "select.{device_name}_solis_energy_storage_control_switch",
            CONTROL_BACKUP_MODE_SOC: "number.{device_name}_solis_backup_soc",
        },
    }

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_timed_mode()
        await self._set_time(CONTROL_TIMED_CHARGE_START_HOURS, start)
        await self._set_time(CONTROL_TIMED_CHARGE_END_HOURS, end)
        await self._set_number(CONTROL_TIMED_CHARGE_SOC, self._target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_CHARGE_CURRENT, await self._power_to_current(power))
        await self._press(CONTROL_TIMED_CHARGE_BUTTON)

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_timed_mode()
        await self._set_time(CONTROL_TIMED_DISCHARGE_START_HOURS, start)
        await self._set_time(CONTROL_TIMED_DISCHARGE_END_HOURS, end)
        await self._set_number(CONTROL_TIMED_DISCHARGE_SOC, self._discharge_target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_DISCHARGE_CURRENT, await self._power_to_current(power))
        await self._press(CONTROL_TIMED_DISCHARGE_BUTTON)

    async def control_idle(self) -> None:
        await self.set_mode(SOLIS_CLOUD_IDLE_MODE)

    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        """The optimiser only asks about "idle"."""
        if state != "idle":
            return False
        return await self.get_mode() == SOLIS_CLOUD_IDLE_MODE

    async def _set_timed_mode(self) -> None:
        if await self.get_mode() != SOLIS_CLOUD_TIMED_MODE:
            await self.set_mode(SOLIS_CLOUD_TIMED_MODE)


class SolisConnectInverter(SolisInverter):
    """Solis inverter controlled through the SolisConnect integration."""

    integration: ClassVar[str] = "solisconnect"
    control_mode: ClassVar[str | None] = SOLIS_CONNECT_IDLE_MODE
    backup_control_mode: ClassVar[str | None] = SOLIS_CONNECT_BACKUP_MODE
    entity_defs: ClassVar[dict[str, dict[str, str]]] = {
        "model_entities": {
            MODEL_BATTERY_SOC: "sensor.{device_name}_battery_soc",
            MODEL_GRID_IMPORT_TODAY: "sensor.{device_name}_today_energy_imported_from_grid",
            MODEL_GRID_EXPORT_TODAY: "sensor.{device_name}_today_energy_fed_into_grid",
            MODEL_CONSUMPTION_TODAY: [
                "sensor.{device_name}_household_load_today_energy",
                "sensor.{device_name}_today_energy_consumption",
            ],
            MODEL_BATTERY_MINIMUM_SOC: "number.{device_name}_overdischarge_soc",
        },
        "control_entities": {
            CONTROL_BATTERY_VOLTAGE: "sensor.{device_name}_battery_voltage",
            CONTROL_TIMED_CHARGE_ON: [
                "switch.{device_name}_timed_charge_enable_1",
                "switch.{device_name}_grid_time_of_use_charging_period_1",
            ],
            CONTROL_TIMED_CHARGE_START_HOURS: [
                "time.{device_name}_timed_charge_start_1",
                "time.{device_name}_grid_time_of_use_charge_start_slot_1",
            ],
            CONTROL_TIMED_CHARGE_END_HOURS: [
                "time.{device_name}_timed_charge_end_1",
                "time.{device_name}_grid_time_of_use_charge_end_slot_1",
            ],
            CONTROL_TIMED_CHARGE_CURRENT: [
                "number.{device_name}_timed_charge_current_1",
                "number.{device_name}_grid_time_of_use_charge_battery_current_slot_1",
            ],
            CONTROL_TIMED_CHARGE_SOC: [
                "number.{device_name}_timed_charge_soc_1",
                "number.{device_name}_grid_time_of_use_charge_cut_off_soc_slot_1",
            ],
            CONTROL_TIMED_DISCHARGE_ON: [
                "switch.{device_name}_timed_discharge_enable_1",
                "switch.{device_name}_grid_time_of_use_discharge_period_1",
            ],
            CONTROL_TIMED_DISCHARGE_START_HOURS: [
                "time.{device_name}_timed_discharge_start_1",
                "time.{device_name}_grid_time_of_use_discharge_start_slot_1",
            ],
            CONTROL_TIMED_DISCHARGE_END_HOURS: [
                "time.{device_name}_timed_discharge_end_1",
                "time.{device_name}_grid_time_of_use_discharge_end_slot_1",
            ],
            CONTROL_TIMED_DISCHARGE_CURRENT: [
                "number.{device_name}_timed_discharge_current_1",
                "number.{device_name}_grid_time_of_use_discharge_battery_current_slot_1",
            ],
            CONTROL_TIMED_DISCHARGE_SOC: [
                "number.{device_name}_timed_discharge_soc_1",
                "number.{device_name}_grid_time_of_use_discharge_cut_off_soc_slot_1",
            ],
            CONTROL_INVERTER_MODE: "select.{device_name}_work_mode",
            CONTROL_BACKUP_MODE_SOC: [
                "number.{device_name}_backup_mode_soc",
                "number.{device_name}_backup_soc",
            ],
            SOLIS_CONNECT_STORAGE_CONTROL: "sensor.{device_name}_storage_control_switching_value",
            SOLIS_CONNECT_TOU_SWITCH: "sensor.{device_name}_time_of_use_v2_switch",
            **{
                f"tou_{name}_{part}": f"sensor.{{device_name}}_grid_time_of_use_{name}_{part}_slot_1"
                for name in SOLIS_CONNECT_TOU_TIMES.values()
                for part in ("hour", "minute")
            },
        },
    }

    def _slot_enabled(self, key: str) -> bool:
        bit = SOLIS_CONNECT_TOU_SWITCH_BITS.get(key)
        if bit is not None and self._entity_id(SOLIS_CONNECT_TOU_SWITCH) is not None:
            value = self._numeric_state(SOLIS_CONNECT_TOU_SWITCH)
            if value is not None:
                return bool(int(value) >> bit & 1)
        return self._switch_on(key)

    def _programmed_time(self, hours_key: str, minutes_key: str) -> tuple[int, int] | None:
        name = SOLIS_CONNECT_TOU_TIMES.get(hours_key)
        if name is not None and self._entity_id(f"tou_{name}_hour") is not None:
            return super()._programmed_time(f"tou_{name}_hour", f"tou_{name}_minute")
        return super()._programmed_time(hours_key, minutes_key)

    async def control_charge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_base_mode()
        await self._turn_off_if_on(CONTROL_TIMED_DISCHARGE_ON)
        await self._clear_slot_times(CONTROL_TIMED_DISCHARGE_START_HOURS, CONTROL_TIMED_DISCHARGE_START_MINUTES,
                                     CONTROL_TIMED_DISCHARGE_END_HOURS, CONTROL_TIMED_DISCHARGE_END_MINUTES)
        await self._set_time(CONTROL_TIMED_CHARGE_START_HOURS, start)
        await self._set_time(CONTROL_TIMED_CHARGE_END_HOURS, end)
        await self._set_number(CONTROL_TIMED_CHARGE_SOC, self._target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_CHARGE_CURRENT, await self._power_to_current(power))
        await self._turn_on(CONTROL_TIMED_CHARGE_ON)

    async def control_discharge(self, start: datetime, end: datetime, target_soc: float, power: float) -> None:
        await self._set_base_mode()
        await self._turn_off_if_on(CONTROL_TIMED_CHARGE_ON)
        await self._clear_slot_times(CONTROL_TIMED_CHARGE_START_HOURS, CONTROL_TIMED_CHARGE_START_MINUTES,
                                     CONTROL_TIMED_CHARGE_END_HOURS, CONTROL_TIMED_CHARGE_END_MINUTES)
        await self._set_time(CONTROL_TIMED_DISCHARGE_START_HOURS, start)
        await self._set_time(CONTROL_TIMED_DISCHARGE_END_HOURS, end)
        await self._set_number(CONTROL_TIMED_DISCHARGE_SOC, self._discharge_target_soc(target_soc))
        await self._set_current(CONTROL_TIMED_DISCHARGE_CURRENT, await self._power_to_current(power))
        await self._turn_on(CONTROL_TIMED_DISCHARGE_ON)

    async def _clear_slot_times(self, start_hours, start_minutes, end_hours, end_minutes) -> None:
        """Set the other slot's times to 00:00-00:00 before writing a window (audit 18).

        The Solis rejects slot times that overlap another slot's times, even a disabled slot, and about
        30 s later restores the last valid times for both.
        """
        if self._programmed_time(start_hours, start_minutes) == (0, 0) and self._programmed_time(end_hours, end_minutes) == (0, 0):
            return
        for key in (start_hours, end_hours):
            await self.hass.services.async_call(
                "time", "set_value", {"entity_id": self._required_entity_id(key), "time": "00:00:00"}, blocking=True
            )

    async def control_idle(self) -> None:
        await self._turn_off(CONTROL_TIMED_CHARGE_ON)
        await self._turn_off(CONTROL_TIMED_DISCHARGE_ON)
        await self.set_mode(self.active_control_mode)

    async def control_matches(self, state: str, target_soc: float | None, power: float) -> bool:
        """The optimiser only asks about "idle"."""
        if state != "idle":
            return False
        return (
            await self.get_mode() == self.active_control_mode
            and not self._slot_enabled(CONTROL_TIMED_CHARGE_ON)
            and not self._slot_enabled(CONTROL_TIMED_DISCHARGE_ON)
        )

    async def get_mode(self) -> str | None:
        """Current work mode, read from the storage-control register.

        The work-mode select can sit at "unknown" indefinitely. Taking that at
        face value made Miser re-select the mode on every run, and selecting
        Backup/Reserve resets the inverter's Backup SOC to 99%, so a reserve
        set by the user was wiped within minutes. The register is reliable.
        """
        if self._entity_id(SOLIS_CONNECT_STORAGE_CONTROL) is not None:
            value = self._numeric_state(SOLIS_CONNECT_STORAGE_CONTROL)
            if value is not None and int(value) in SOLIS_CONNECT_MODES_BY_REGISTER:
                return SOLIS_CONNECT_MODES_BY_REGISTER[int(value)]
        state = self._state(self._required_entity_id(CONTROL_INVERTER_MODE))
        return None if state is None else state.state

    async def _set_base_mode(self) -> None:
        """Self-Use normally; Backup/Reserve while Miser's Backup Mode is on."""
        mode = self.active_control_mode
        if await self.get_mode() != mode:
            await self.set_mode(mode)


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
