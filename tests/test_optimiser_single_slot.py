"""Only one timed slot is ever programmed at a time.

Given a charge and a discharge slot that both cover the present, a Solis just
charges: on 24 Oct Miser reported "Discharging" for four hours while the battery
charged.
"""

import pandas as pd
import pytest

from custom_components.miser.optimiser import (
    _apply_inverter_control,
    _control_slots,
    _find_current_control_slot,
    _slot_to_apply,
)
from tests.test_optimiser_control_windows import _FakeHass, _FakeModel, _flows, _RecordingController


def _discharge_now_charge_soon() -> pd.DataFrame:
    """A discharge segment running now, with a charge starting five minutes from now."""
    now = pd.Timestamp.now(tz="UTC")
    boundary = now + pd.Timedelta(minutes=5)
    index = pd.DatetimeIndex([boundary - pd.Timedelta(minutes=30), boundary, boundary + pd.Timedelta(minutes=30)])
    return pd.DataFrame(
        {"forced": [-3000.0, 3000.0, 3000.0], "soc_end": [40.0, 47.0, 54.0], "dt_hours": [0.5, 0.5, 0.5]},
        index=index,
    )


@pytest.mark.asyncio
async def test_running_discharge_is_not_programmed_alongside_upcoming_charge() -> None:
    controller = _RecordingController()
    hass = _FakeHass(controller)

    await _apply_inverter_control(hass, _FakeModel(_discharge_now_charge_soon()), schedule_checks=False)

    assert len(controller.discharge_calls) == 1
    assert controller.charge_calls == []


def test_idle_now_programmes_only_the_earliest_upcoming_slot() -> None:
    now = pd.Timestamp("2026-10-24 03:55", tz="UTC")
    slots = _control_slots(_flows([("2026-10-24 04:00", -2000.0, 80.0), ("2026-10-24 04:30", 3000.0, 85.0)]))

    chosen = _slot_to_apply(slots, now, pd.Timedelta(minutes=40), None)

    assert [slot["state"] for slot in chosen] == ["discharging"]


def test_current_slot_wins_over_upcoming() -> None:
    now = pd.Timestamp("2026-10-24 04:25", tz="UTC")
    slots = _control_slots(_flows([("2026-10-24 04:00", -2000.0, 80.0), ("2026-10-24 04:30", 3000.0, 85.0)]))
    current = _find_current_control_slot(slots, now)

    chosen = _slot_to_apply(slots, now, pd.Timedelta(minutes=10), current)

    assert [slot["state"] for slot in chosen] == ["discharging"]


def test_nothing_running_or_due_programmes_nothing() -> None:
    now = pd.Timestamp("2026-10-24 02:00", tz="UTC")
    slots = _control_slots(_flows([("2026-10-24 04:00", 3000.0, 80.0)]))

    assert _slot_to_apply(slots, now, pd.Timedelta(minutes=10), None) == []
