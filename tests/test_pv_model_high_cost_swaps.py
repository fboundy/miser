"""High-cost swaps with no slot that can take a charge.

Regression: when every earlier slot was filtered out (battery already near
full), high_cost_swaps() indexed an empty charge window and the whole optimiser
run died with IndexError.
"""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


@pytest.mark.asyncio
async def test_no_charge_headroom_returns_no_swaps() -> None:
    # A 98% floor keeps every slot's SOC above the 97% search limit, so the
    # search window always filters down to nothing.
    model = PVsystemModel(
        inverter=InverterModel(),
        battery=BatteryModel(battery_capacity=10000, battery_minimum_soc=98, battery_current_limit=60),
    )
    idx = pd.date_range("2026-10-30 12:00", periods=6, freq="30min", tz="UTC")
    model.consumption = pd.Series(1000.0, index=idx, name="consumption")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    model.prices = pd.DataFrame({"import": [10, 10, 10, 30, 30, 30], "export": 0.0}, index=idx)
    model.initial_soc = 100
    model.base_cost = (await model.net_cost(slots=[])).sum()

    assert await model.high_cost_swaps() == []
    assert model.best_cost == model.base_cost
