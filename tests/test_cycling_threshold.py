"""Cycling Threshold: keep forced exports worth the cycle, drop small ones."""

import asyncio

import pandas as pd
import pytest

from custom_components.miser.const import CONF_CYCLING_THRESHOLD, DOMAIN
from custom_components.miser.optimiser import _calculate_cost_and_flows, _drop_small_cycles
from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel

START = pd.Timestamp("2026-10-25 18:30", tz="UTC")


class _State:
    def __init__(self, state) -> None:
        self.state = state
        self.attributes = {}


class _ConfigEntries:
    def async_entries(self, domain):
        return []


class _Hass:
    def __init__(self, threshold: str) -> None:
        states = {"number.miser_cycling_threshold": _State(threshold)}
        self.states = type("S", (), {"get": lambda _self, entity_id: states.get(entity_id)})()
        self.config_entries = _ConfigEntries()
        self.data = {
            DOMAIN: {
                "config_entities": {CONF_CYCLING_THRESHOLD: "number.miser_cycling_threshold"},
                "model_entities": {},
                "control_entities": {},
            }
        }

    async def async_add_executor_job(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)


def _model() -> PVsystemModel:
    """Evening at 32.87p import, 7.43p from midnight, 13p export throughout."""
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=97, charger_efficiency=91, charger_power=3000, inverter_power=3600),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=15, battery_current_limit=60, voltage=50),
    )
    index = pd.date_range(START, periods=25, freq="30min")
    import_price = [32.87 if t < pd.Timestamp("2026-10-26 00:00", tz="UTC") else 7.43 for t in index]
    model.solar = pd.Series(0.0, index=index, name="solar")
    model.consumption = pd.Series(400.0, index=index, name="consumption")
    model.prices = pd.DataFrame({"import": import_price, "export": 13.0}, index=index)
    model.initial_soc = 64
    return model


def _at(hhmm: str, day: int = 26) -> pd.Timestamp:
    return pd.Timestamp(f"2026-10-{day} {hhmm}", tz="UTC")


def _plan() -> list:
    """One big evening export, a cheap-window recharge, and a small dip inside it."""
    slots = [(_at(t, 25), -3000.0) for t in ("20:00", "20:30", "21:00", "21:30")]  # ~6 kWh out
    slots += [(_at(t), 3297.0) for t in ("00:00", "00:30", "01:00", "01:30", "02:00", "02:30")]
    slots += [(_at("03:00"), -2000.0)]  # small mid-window dip
    slots += [(_at(t), 3297.0) for t in ("03:30", "04:00", "04:30")]
    return slots


async def _optimised(model: PVsystemModel) -> PVsystemModel:
    model.optimised_slots = _plan()
    model.optimised_cost, model.optimised_flows = await _calculate_cost_and_flows(model, model.optimised_slots)
    model.best_cost = model.optimised_cost
    return model


def _exports(slots) -> set:
    return {start for start, power in slots if power < 0}


@pytest.mark.asyncio
async def test_small_dip_dropped_big_evening_export_kept() -> None:
    model = await _optimised(_model())
    before = model.optimised_cost

    await _drop_small_cycles(_Hass("20"), model)

    assert _at("03:00") not in _exports(model.optimised_slots)
    assert {_at(t, 25) for t in ("20:00", "20:30", "21:00", "21:30")} <= _exports(model.optimised_slots)
    # Dropping a cycle can only cost something, and less than the threshold.
    assert before <= model.optimised_cost < before + 20


@pytest.mark.asyncio
async def test_zero_threshold_keeps_every_cycle() -> None:
    model = await _optimised(_model())
    slots = list(model.optimised_slots)

    await _drop_small_cycles(_Hass("0"), model)

    assert model.optimised_slots == slots


@pytest.mark.asyncio
async def test_high_threshold_drops_all_exports() -> None:
    model = await _optimised(_model())

    await _drop_small_cycles(_Hass("100"), model)

    assert _exports(model.optimised_slots) == set()
    # Charging is untouched.
    assert any(power > 0 for _, power in model.optimised_slots)


@pytest.mark.asyncio
async def test_premium_export_survives_any_sensible_threshold() -> None:
    """An Axle-style event at 100p export is worth far more than the threshold."""
    model = _model()
    model.prices.loc[_at("20:00", 25) : _at("21:30", 25), "export"] = 100.0
    model = await _optimised(model)

    await _drop_small_cycles(_Hass("50"), model)

    assert {_at(t, 25) for t in ("20:00", "20:30", "21:00", "21:30")} <= _exports(model.optimised_slots)
