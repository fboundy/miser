"""A small change in charge/discharge power keeps the power already on the inverter (10% of the battery limit)."""

from types import SimpleNamespace

import pandas as pd
import pytest

from custom_components.miser.optimiser import _programme_control_slot
from tests.test_optimiser_control_windows import _FakeHass, _RecordingController

MODEL = SimpleNamespace(
    inverter=SimpleNamespace(charger_efficiency=1.0, inverter_efficiency=1.0),
    battery_charge_limit=3000.0,
    battery_discharge_limit=3000.0,
)


class _Holding(_RecordingController):
    def __init__(self, held: float | None) -> None:
        super().__init__()
        self.held = held

    async def programmed_power(self, state):
        return self.held


def _slot(power: float) -> dict:
    start = pd.Timestamp.now(tz="UTC").floor("30min")
    return {"start": start, "end": start + pd.Timedelta(hours=2), "state": "charging" if power > 0 else "discharging",
            "power": power, "target_soc": 90.0}


async def _written_power(held, power) -> float:
    controller = _Holding(held)
    await _programme_control_slot(_FakeHass(controller), controller, _slot(power), MODEL)
    calls = controller.charge_calls or controller.discharge_calls
    return calls[0][3]


@pytest.mark.asyncio
async def test_change_below_threshold_keeps_the_programmed_power() -> None:
    assert await _written_power(2500.0, 2750.0) == 2500.0  # 250 W < 300 W
    assert await _written_power(2500.0, -2300.0) == 2500.0  # discharge too


@pytest.mark.asyncio
async def test_change_at_or_above_threshold_is_written() -> None:
    assert await _written_power(2500.0, 2800.0) == 2800.0  # 300 W = 10% of 3 kW


@pytest.mark.asyncio
async def test_controller_that_cannot_read_back_always_gets_the_new_power() -> None:
    assert await _written_power(None, 2750.0) == 2750.0
