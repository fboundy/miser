"""Solar surplus charges the battery DC to DC without the charger loss, and a short leading
control fragment runs at its window's next power."""

from types import SimpleNamespace

import pandas as pd
import pytest

from custom_components.miser.optimiser import _battery_side_power, _short_lead_takes_next_power
from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model() -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=97, charger_efficiency=91, charger_power=3000, inverter_power=3600),
        battery=BatteryModel(battery_capacity=10000, battery_minimum_soc=15, battery_current_limit=100, voltage=50),
    )
    model.initial_soc = 50
    index = pd.date_range("2026-11-09 12:00", periods=2, freq="30min", tz="UTC")
    model.solar = pd.Series([2000.0, 2000.0], index=index, name="solar")
    model.consumption = pd.Series([1000.0, 1000.0], index=index, name="consumption")
    model.prices = pd.DataFrame({"import": 32.87, "export": 13.0}, index=index)
    return model


def test_solar_surplus_charges_without_loss() -> None:
    assert _model()._whole_horizon_transition(5000.0, -1000.0, None, 0.5) == (5500.0, 0.0)


def test_charge_below_the_surplus_exports_the_rest() -> None:
    assert _model()._whole_horizon_transition(5000.0, -1000.0, 500.0, 0.5) == (5250.0, -500.0)


def test_charge_above_the_surplus_takes_the_rest_through_the_charger() -> None:
    energy, grid = _model()._whole_horizon_transition(5000.0, -1000.0, 1500.0, 0.5)
    assert energy == pytest.approx(5000 + (1000 + 500 * 0.91) * 0.5, abs=0.1)
    assert grid == 500.0


@pytest.mark.asyncio
async def test_flows_store_the_whole_surplus() -> None:
    flows = await _model().flows(slots=[])
    assert flows["soc_end"].iloc[-1] == pytest.approx(60.0)
    assert (flows["grid"] == 0).all()


def test_command_uses_the_slot_surplus() -> None:
    start = pd.Timestamp("2026-11-09 12:00", tz="UTC")
    flows = pd.DataFrame({"solar": [2000.0], "consumption": [1000.0]}, index=[start])
    model = SimpleNamespace(inverter=SimpleNamespace(charger_efficiency=0.91), optimised_flows=flows)
    assert _battery_side_power(model, "charging", 1500.0, start + pd.Timedelta(minutes=8)) == pytest.approx(1000 + 500 * 0.91)
    assert _battery_side_power(model, "charging", 1500.0) == pytest.approx(1500 * 0.91)


def _slot(start: str, end: str, power: float, window_start: str = "12:28") -> dict:
    day = "2026-11-09 "
    return {
        "start": pd.Timestamp(day + start, tz="UTC"),
        "end": pd.Timestamp(day + end, tz="UTC"),
        "window_start": pd.Timestamp(day + window_start, tz="UTC"),
        "state": "charging",
        "power": power,
    }


def test_short_leading_fragment_takes_the_next_power() -> None:
    slots = [_slot("12:28", "12:30", 3000.0), _slot("12:30", "13:30", 1000.0)]
    assert [s["power"] for s in _short_lead_takes_next_power(slots, pd.Timedelta(minutes=10))] == [1000.0, 1000.0]


def test_full_length_or_separate_window_keeps_its_power() -> None:
    long_lead = [_slot("12:08", "12:30", 800.0, "12:08"), _slot("12:30", "13:30", 1000.0, "12:08")]
    other_window = [_slot("12:28", "12:30", 3000.0), _slot("12:30", "13:30", 1000.0, "12:30")]
    for slots in (long_lead, other_window):
        assert _short_lead_takes_next_power(slots, pd.Timedelta(minutes=10)) == slots
