"""Negative prices: an import at a negative price earns, an export at a negative price costs (#10)."""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model() -> tuple[PVsystemModel, pd.DatetimeIndex]:
    idx = pd.date_range("2026-11-04 00:00", periods=4, freq="30min", tz="UTC")
    model = PVsystemModel(
        inverter=InverterModel(97, 91, 3600, 3000),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=15, battery_current_limit=60),
    )
    model.consumption = pd.Series(0.0, index=idx)
    model.solar = pd.Series(0.0, index=idx)
    model.prices = pd.DataFrame({"import": -5.0, "export": -2.0}, index=idx)
    model.initial_soc = 50
    return model, idx


@pytest.mark.asyncio
async def test_import_at_a_negative_price_is_priced_at_the_import_price() -> None:
    model, idx = _model()
    flows = await model.flows(slots=[(start, 3000.0) for start in idx])

    imported_kwh = (flows["grid"] * flows["dt_hours"]).sum() / 1000
    assert flows["net_cost"].sum() == pytest.approx(imported_kwh * -5.0)


@pytest.mark.asyncio
async def test_export_at_a_negative_price_costs_the_export_price() -> None:
    model, idx = _model()
    flows = await model.flows(slots=[(start, -3000.0) for start in idx])

    exported_kwh = -(flows["grid"] * flows["dt_hours"]).sum() / 1000
    assert flows["net_cost"].sum() == pytest.approx(exported_kwh * 2.0)
