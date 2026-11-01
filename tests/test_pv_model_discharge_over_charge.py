"""A high export price must win over a cheap charge planned in the same slot.

Regression (2026-11-01 06:00 Axle event): low-cost charging put a top-up in the
100p export slots and the discharge pass skipped any slot with a planned charge,
so the battery charged through the export event instead of exporting.
"""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


@pytest.mark.asyncio
async def test_discharge_replaces_charge_in_high_export_slot() -> None:
    idx = pd.date_range("2026-11-01 06:00", "2026-11-02 00:00", freq="30min", tz="UTC", inclusive="left")
    model = PVsystemModel(
        inverter=InverterModel(97, 91, 3600, 3000),
        battery=BatteryModel(battery_capacity=10000, battery_minimum_soc=15, battery_current_limit=60),
    )
    model.consumption = pd.Series(500.0, index=idx, name="consumption")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    imp = pd.Series(25.0, index=idx)
    imp[idx.hour < 7] = 12.0
    exp = pd.Series(15.0, index=idx)
    exp[idx.hour == 6] = 100.0
    model.prices = pd.DataFrame({"import": imp, "export": exp})
    model.initial_soc = 96
    model.base_cost = model.best_cost = (await model.net_cost(slots=[])).sum()

    slots = await model.discharging(base_slots=await model.low_cost_charging(base_slots=await model.high_cost_swaps()))
    forced = (await model.flows(slots=slots))["forced"]

    assert (forced[idx.hour == 6] < 0).all()
