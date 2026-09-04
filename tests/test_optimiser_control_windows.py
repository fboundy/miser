"""Tests for control-window derivation of inverter charge/discharge programming.

Regression cover for the 2026-08-06 overnight incident: a charge window that
started correctly at full power was stopped within ten minutes because each
optimiser run reprogrammed the inverter from the *current fragment* of the
window rather than the window itself, writing an end time a few minutes away
and a target SOC about one percent above present charge.
"""

import pandas as pd
import pytest

from custom_components.miser.const import COST_ENTITY_OBJECTS, DOMAIN
from custom_components.miser.optimiser import (
    _apply_inverter_control,
    _control_slots,
    _find_current_control_slot,
    _find_next_control_slot,
    _programmed_window_overlaps,
    _slots_to_apply,
)


def _flows(rows: list[tuple[str, float, float]], dt_hours: float = 0.5) -> pd.DataFrame:
    """Build a flows frame from (start, forced_power, soc_end) rows."""
    index = pd.DatetimeIndex([pd.Timestamp(start, tz="UTC") for start, _, _ in rows])
    return pd.DataFrame(
        {
            "forced": [power for _, power, _ in rows],
            "soc_end": [soc for _, _, soc in rows],
            "dt_hours": [dt_hours] * len(rows),
        },
        index=index,
    )


def _overnight_charge() -> pd.DataFrame:
    """A 23:30 -> 04:30 charge from 15% to 83%, tapering as it approaches target."""
    return _flows(
        [
            ("2026-08-06 23:30", 3000.0, 22.0),
            ("2026-08-07 00:00", 3000.0, 29.0),
            ("2026-08-07 00:30", 3000.0, 36.0),
            ("2026-08-07 01:00", 3000.0, 43.0),
            ("2026-08-07 01:30", 3000.0, 50.0),
            ("2026-08-07 02:00", 3000.0, 57.0),
            ("2026-08-07 02:30", 3000.0, 64.0),
            ("2026-08-07 03:00", 3000.0, 71.0),
            ("2026-08-07 03:30", 3000.0, 78.0),
            # Taper: outside the 10% power tolerance, so a new power segment.
            ("2026-08-07 04:00", 1200.0, 83.0),
        ]
    )


def test_window_spans_all_segments_despite_power_change() -> None:
    slots = _control_slots(_overnight_charge())

    # The taper creates a second power segment...
    assert len(slots) == 2
    assert slots[0]["power"] == 3000.0
    assert slots[1]["power"] == 1200.0

    # ...but both belong to one control window covering the whole charge.
    for slot in slots:
        assert slot["window_start"] == pd.Timestamp("2026-08-06 23:30", tz="UTC")
        assert slot["window_end"] == pd.Timestamp("2026-08-07 04:30", tz="UTC")
        assert slot["window_target_soc"] == 83.0


def test_midwindow_run_still_programmes_full_window() -> None:
    """The incident: an optimiser run at 00:56, inside the window.

    The active segment ends at 01:00 with soc_end 36%, but the inverter must
    still be given the window end (04:30) and the window target (83%).
    """
    slots = _control_slots(_overnight_charge())
    now = pd.Timestamp("2026-08-07 00:56", tz="UTC")

    current = _find_current_control_slot(slots, now)
    assert current is not None
    assert current["state"] == "charging"

    # The fragment's own values are what used to be written, and are wrong.
    assert current["end"] < pd.Timestamp("2026-08-07 04:30", tz="UTC")
    assert current["target_soc"] < 83.0

    # The window values are what must be written.
    assert current["window_end"] == pd.Timestamp("2026-08-07 04:30", tz="UTC")
    assert current["window_target_soc"] == 83.0


def test_applied_slot_carries_window_bounds_not_fragment() -> None:
    slots = _control_slots(_overnight_charge())
    now = pd.Timestamp("2026-08-07 00:56", tz="UTC")

    to_apply = _slots_to_apply(slots, now, pd.Timedelta(minutes=10))
    assert len(to_apply) == 1

    slot = to_apply[0]
    assert slot["window_start"] == pd.Timestamp("2026-08-06 23:30", tz="UTC")
    assert slot["window_end"] == pd.Timestamp("2026-08-07 04:30", tz="UTC")
    assert slot["window_target_soc"] == 83.0
    # Power fidelity is preserved: the segment running at 00:56 is the 3 kW one.
    assert slot["power"] == 3000.0


def test_target_soc_is_stable_across_the_window() -> None:
    """Every run inside the window must programme the same target.

    Previously the target tracked the current fragment, so it decayed to just
    above present SOC and the inverter stopped charging.
    """
    slots = _control_slots(_overnight_charge())

    targets = set()
    for minutes in range(0, 300, 10):
        now = pd.Timestamp("2026-08-06 23:30", tz="UTC") + pd.Timedelta(minutes=minutes)
        to_apply = _slots_to_apply(slots, now, pd.Timedelta(minutes=10))
        if to_apply:
            targets.add(to_apply[0]["window_target_soc"])

    assert targets == {83.0}


def test_direction_change_starts_a_new_window() -> None:
    flows = _flows(
        [
            ("2026-08-06 21:00", -2000.0, 20.0),
            ("2026-08-06 21:30", -2000.0, 15.0),
            ("2026-08-06 22:00", 3000.0, 22.0),
            ("2026-08-06 22:30", 3000.0, 29.0),
        ]
    )
    slots = _control_slots(flows)

    assert [slot["state"] for slot in slots] == ["discharging", "charging"]
    assert slots[0]["window_end"] == pd.Timestamp("2026-08-06 22:00", tz="UTC")
    assert slots[0]["window_target_soc"] == 15.0
    assert slots[1]["window_start"] == pd.Timestamp("2026-08-06 22:00", tz="UTC")
    assert slots[1]["window_target_soc"] == 29.0


def test_gap_starts_a_new_window() -> None:
    """Non-contiguous forced periods are separate windows even when same-direction."""
    flows = _flows(
        [
            ("2026-08-06 23:30", 3000.0, 22.0),
            # 04:00 leaves a gap after the 00:00 end of the first period.
            ("2026-08-07 04:00", 3000.0, 30.0),
        ]
    )
    slots = _control_slots(flows)

    assert len(slots) == 2
    assert slots[0]["window_end"] == pd.Timestamp("2026-08-07 00:00", tz="UTC")
    assert slots[1]["window_start"] == pd.Timestamp("2026-08-07 04:00", tz="UTC")


def test_next_slot_is_the_next_window_not_the_next_segment() -> None:
    """While a window runs, "next slot" must not point at its own taper."""
    slots = _control_slots(_overnight_charge())
    now = pd.Timestamp("2026-08-07 00:56", tz="UTC")

    assert _find_next_control_slot(slots, now) is None


def test_next_slot_reports_upcoming_window_bounds() -> None:
    slots = _control_slots(_overnight_charge())
    now = pd.Timestamp("2026-08-06 22:00", tz="UTC")

    next_slot = _find_next_control_slot(slots, now)
    assert next_slot is not None
    assert next_slot["window_start"] == pd.Timestamp("2026-08-06 23:30", tz="UTC")
    assert next_slot["window_end"] == pd.Timestamp("2026-08-07 04:30", tz="UTC")
    assert next_slot["window_target_soc"] == 83.0


def test_single_segment_window_is_unchanged() -> None:
    flows = _flows(
        [
            ("2026-08-06 23:30", 3000.0, 22.0),
            ("2026-08-07 00:00", 3000.0, 29.0),
        ]
    )
    slots = _control_slots(flows)

    assert len(slots) == 1
    slot = slots[0]
    assert slot["start"] == slot["window_start"]
    assert slot["end"] == slot["window_end"]
    assert slot["target_soc"] == slot["window_target_soc"] == 29.0


# --- Axle VPP exclusion must use the interval that is actually programmed ---


def test_axle_overlap_uses_programmed_window_not_segment() -> None:
    """An event clear of the running segment can still overlap the window.

    The inverter is programmed with the whole window, so excluding on segment
    bounds would let the early segment through and then programme straight
    across the Axle event.
    """
    slots = _control_slots(_overnight_charge())
    early_segment = slots[0]

    # Event sits inside the taper segment only (04:00 -> 04:30).
    axle_window = {
        "start": pd.Timestamp("2026-08-07 04:05", tz="UTC"),
        "end": pd.Timestamp("2026-08-07 04:25", tz="UTC"),
    }

    # Segment bounds alone see no overlap - the old, unsafe comparison.
    assert early_segment["end"] <= axle_window["start"]

    # The programmed window does overlap, so the segment must be excluded.
    assert _programmed_window_overlaps(early_segment, axle_window)


def test_programmed_window_overlap_is_false_when_window_is_clear() -> None:
    slots = _control_slots(_overnight_charge())
    axle_window = {
        "start": pd.Timestamp("2026-08-07 06:00", tz="UTC"),
        "end": pd.Timestamp("2026-08-07 07:00", tz="UTC"),
    }

    assert not any(_programmed_window_overlaps(slot, axle_window) for slot in slots)


# --- Discharge windows get the same treatment ---


def _evening_discharge() -> pd.DataFrame:
    """A 17:00 -> 21:00 discharge from 80% down to 15%, easing off at the end."""
    return _flows(
        [
            ("2026-08-06 17:00", -2800.0, 72.0),
            ("2026-08-06 17:30", -2800.0, 64.0),
            ("2026-08-06 18:00", -2800.0, 56.0),
            ("2026-08-06 18:30", -2800.0, 48.0),
            ("2026-08-06 19:00", -2800.0, 40.0),
            ("2026-08-06 19:30", -2800.0, 32.0),
            # Eases off outside the 10% tolerance -> second power segment.
            ("2026-08-06 20:00", -900.0, 23.0),
            ("2026-08-06 20:30", -900.0, 15.0),
        ]
    )


def test_discharge_window_spans_all_segments() -> None:
    slots = _control_slots(_evening_discharge())

    assert len(slots) == 2
    assert [slot["state"] for slot in slots] == ["discharging", "discharging"]
    assert slots[0]["power"] == -2800.0
    assert slots[1]["power"] == -900.0

    for slot in slots:
        assert slot["window_start"] == pd.Timestamp("2026-08-06 17:00", tz="UTC")
        assert slot["window_end"] == pd.Timestamp("2026-08-06 21:00", tz="UTC")
        assert slot["window_target_soc"] == 15.0


def test_midwindow_discharge_keeps_segment_power_and_window_target() -> None:
    slots = _control_slots(_evening_discharge())
    now = pd.Timestamp("2026-08-06 18:45", tz="UTC")

    to_apply = _slots_to_apply(slots, now, pd.Timedelta(minutes=10))
    assert len(to_apply) == 1

    slot = to_apply[0]
    assert slot["power"] == -2800.0
    assert slot["window_end"] == pd.Timestamp("2026-08-06 21:00", tz="UTC")
    assert slot["window_target_soc"] == 15.0
    # The fragment's own target would have been far higher, i.e. barely a discharge.
    assert slot["target_soc"] > slot["window_target_soc"]


# --- Application boundary: what actually reaches the inverter controller ---


class _RecordingController:
    """Minimal inverter controller that records what it was asked to do."""

    def __init__(self) -> None:
        self.charge_calls: list[tuple] = []
        self.discharge_calls: list[tuple] = []
        self.idle_calls = 0

    async def power_to_current(self, power: float) -> float:
        return abs(float(power)) / 50.0

    async def control_charge(self, start, end, target_soc, power) -> None:
        self.charge_calls.append((start, end, target_soc, power))

    async def control_discharge(self, start, end, target_soc, power) -> None:
        self.discharge_calls.append((start, end, target_soc, power))

    async def control_idle(self) -> None:
        self.idle_calls += 1

    async def control_matches(self, state, target_soc, power) -> bool:
        return False


class _FakeStates:
    def get(self, entity_id):  # noqa: D102 - trivial stub
        return None


class _FakeConfigEntries:
    def async_entries(self, domain):  # noqa: D102 - no Axle VPP installed
        return []


class _FakeHass:
    def __init__(self, controller) -> None:
        self.data = {
            DOMAIN: {
                "inverter_controller": controller,
                COST_ENTITY_OBJECTS: {},
                "model_entities": {},
                "control_entities": {},
                "config_entities": {},
            }
        }
        self.states = _FakeStates()
        self.config_entries = _FakeConfigEntries()


class _FakeModel:
    def __init__(self, flows: pd.DataFrame) -> None:
        self.optimised_flows = flows


def _window_around_now(power: float, taper_power: float, socs: list[float]) -> pd.DataFrame:
    """Build a window that is already running, anchored to the real clock.

    `_apply_inverter_control` reads `pd.Timestamp.now`, so the fixture is built
    relative to now rather than trying to freeze time.
    """
    base = pd.Timestamp.now(tz="UTC").floor("30min")
    starts = [base - pd.Timedelta(minutes=60) + pd.Timedelta(minutes=30 * i) for i in range(6)]
    powers = [power, power, power, power, taper_power, taper_power]
    index = pd.DatetimeIndex(starts)
    return pd.DataFrame(
        {"forced": powers, "soc_end": socs, "dt_hours": [0.5] * 6},
        index=index,
    )


@pytest.mark.asyncio
async def test_apply_programmes_charge_with_window_bounds_and_segment_power() -> None:
    controller = _RecordingController()
    hass = _FakeHass(controller)
    flows = _window_around_now(3000.0, 1200.0, [22.0, 29.0, 36.0, 43.0, 50.0, 83.0])
    model = _FakeModel(flows)

    await _apply_inverter_control(hass, model, schedule_checks=False)

    assert len(controller.charge_calls) == 1
    start, end, target_soc, power = controller.charge_calls[0]

    expected_start = flows.index[0]
    expected_end = flows.index[-1] + pd.Timedelta(minutes=30)

    assert pd.Timestamp(start) == expected_start
    assert pd.Timestamp(end) == expected_end
    # Terminal SOC of the whole window, not of the running fragment.
    assert target_soc == 83.0
    # Power of the segment actually running.
    assert power == 3000.0


@pytest.mark.asyncio
async def test_apply_programmes_discharge_with_window_bounds_and_segment_power() -> None:
    controller = _RecordingController()
    hass = _FakeHass(controller)
    flows = _window_around_now(-2800.0, -900.0, [72.0, 64.0, 56.0, 48.0, 40.0, 15.0])
    model = _FakeModel(flows)

    await _apply_inverter_control(hass, model, schedule_checks=False)

    assert len(controller.discharge_calls) == 1
    start, end, target_soc, power = controller.discharge_calls[0]

    expected_start = flows.index[0]
    expected_end = flows.index[-1] + pd.Timedelta(minutes=30)

    assert pd.Timestamp(start) == expected_start
    assert pd.Timestamp(end) == expected_end
    assert target_soc == 15.0
    # control_discharge receives magnitude.
    assert power == 2800.0


@pytest.mark.asyncio
async def test_apply_does_not_shorten_window_on_repeated_runs() -> None:
    """The incident, at the boundary: re-running must not truncate the window."""
    controller = _RecordingController()
    hass = _FakeHass(controller)
    flows = _window_around_now(3000.0, 1200.0, [22.0, 29.0, 36.0, 43.0, 50.0, 83.0])
    model = _FakeModel(flows)

    for _ in range(3):
        await _apply_inverter_control(hass, model, schedule_checks=False)

    ends = {pd.Timestamp(call[1]) for call in controller.charge_calls}
    targets = {call[2] for call in controller.charge_calls}

    assert len(controller.charge_calls) == 3
    assert ends == {flows.index[-1] + pd.Timedelta(minutes=30)}
    assert targets == {83.0}
