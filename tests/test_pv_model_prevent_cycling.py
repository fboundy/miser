"""Prevent Cycling: no forced grid-export discharge outside permitted windows."""

import asyncio

import pandas as pd

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel

WINDOW_START = pd.Timestamp("2026-10-25 01:00", tz="UTC")


def _model() -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=97, charger_efficiency=91, charger_power=3000, inverter_power=3600),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=15, battery_current_limit=60, voltage=50),
    )
    # Overnight at a flat 7.43p import and 13p export: the tariff pair that cycled.
    idx = pd.date_range("2026-10-24 23:00", periods=14, freq="30min", tz="UTC")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    model.consumption = pd.Series(300.0, index=idx, name="consumption")
    model.prices = pd.DataFrame({"import": 7.43, "export": 13.0}, index=idx)
    model.initial_soc = 60
    model.base_cost = 0.0
    model.best_cost = 0.0
    return model


def _discharges(slots) -> list:
    return [(start, power) for start, power in slots if power < 0]


def _plan(model: PVsystemModel) -> list:
    return asyncio.run(model.whole_horizon(soc_step_percent=10, state_resolution_wh=500))


def test_unrestricted_plan_cycles_when_export_beats_import() -> None:
    model = _model()
    model.discharge_windows = None
    assert _discharges(_plan(model)), "export above import should tempt the plan to cycle"


def test_prevent_cycling_removes_forced_discharge() -> None:
    model = _model()
    model.discharge_windows = []
    assert _discharges(_plan(model)) == []


def test_prevent_cycling_still_allows_paid_export_window() -> None:
    model = _model()
    model.prices.loc[WINDOW_START : WINDOW_START + pd.Timedelta("30min"), "export"] = 100.0  # Axle event
    model.discharge_windows = [(WINDOW_START, WINDOW_START + pd.Timedelta(hours=1))]

    discharges = _discharges(_plan(model))

    assert discharges
    assert all(WINDOW_START <= start < WINDOW_START + pd.Timedelta(hours=1) for start, _ in discharges)


def test_discharge_pass_respects_prevent_cycling() -> None:
    model = _model()
    model.discharge_windows = []
    assert _discharges(asyncio.run(model.discharging(base_slots=[]))) == []


def test_allowed_mask_covers_overlapping_slots_only() -> None:
    model = _model()
    flows = asyncio.run(model.flows(slots=[]))
    model.discharge_windows = [(WINDOW_START, WINDOW_START + pd.Timedelta(hours=1))]
    allowed = model.forced_discharge_allowed(flows)
    assert list(allowed[allowed].index) == [WINDOW_START, WINDOW_START + pd.Timedelta("30min")]
