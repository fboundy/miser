"""Whole Horizon prices every control change, keeps one power across a same-price run,
and fills from earlier slots for a valuable export.

Regression (2026-11-02): Whole Horizon planned 3297/2857/440/2198 W across a flat-price night,
and without Whole Horizon nothing charged before a 100p Axle export, so it only exported what
was already in the battery.
"""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model(idx, imp, exp, initial_soc, consumption=500.0) -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(97, 91, 3600, 3000),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=15, battery_current_limit=60),
    )
    model.consumption = pd.Series(consumption, index=idx, name="consumption")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    model.prices = pd.DataFrame({"import": imp, "export": exp}, index=idx)
    model.initial_soc = initial_soc
    return model


@pytest.mark.asyncio
async def test_whole_horizon_charges_a_flat_price_night_at_one_power() -> None:
    idx = pd.date_range("2026-11-03 00:00", periods=14, freq="30min", tz="UTC")
    # Flat night, then a dear day the battery should be filled for.
    imp = pd.Series(7.43, index=idx)
    imp[idx.hour >= 5] = 30.0
    model = _model(idx, imp, 0.0, initial_soc=15, consumption=1500.0)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None

    plan = await model.whole_horizon()
    night = [power for start, power in plan if start.hour < 5]

    assert night, "expected the cheap night to be used"
    assert len(set(night)) == 1


@pytest.mark.asyncio
async def test_averaging_keeps_runs_at_different_prices_apart() -> None:
    idx = pd.date_range("2026-11-03 00:00", periods=4, freq="30min", tz="UTC")
    model = _model(idx, pd.Series([5.0, 5.0, 6.0, 6.0], index=idx), 0.0, initial_soc=15)
    flows = await model.flows(slots=[])

    path = [(idx[0], 3000.0), (idx[1], 1000.0), (idx[2], 2000.0), (idx[3], 400.0)]
    averaged = await model._average_same_price_runs(flows, path)

    assert [p for _s, p in averaged] == [2000.0, 2000.0, 1200.0, 1200.0]


def test_only_a_direction_or_price_change_counts_as_a_write() -> None:
    model = _model(pd.date_range("2026-11-03", periods=1, freq="30min", tz="UTC"), 5.0, 0.0, 15)
    cheap = {"import": 7.43, "export": 13.0}
    dear = {"import": 32.87, "export": 13.0}

    # Averaging gives a same-price run one power, so a power change inside it is never written.
    assert model._whole_horizon_control(3000.0, cheap) == model._whole_horizon_control(2000.0, cheap)
    assert model._whole_horizon_control(3000.0, cheap) != model._whole_horizon_control(3000.0, dear)
    assert model._whole_horizon_control(3000.0, cheap) != model._whole_horizon_control(-3000.0, cheap)
    assert model._whole_horizon_control(None) == model._whole_horizon_control(5.0) == "idle"


@pytest.mark.asyncio
async def test_whole_horizon_precharges_for_a_valuable_export() -> None:
    # 2026-11-02: an Axle 100p export must be filled from cheaper slots before it, not just from what's in the battery.
    idx = pd.date_range("2026-11-02 14:00", "2026-11-02 20:00", freq="30min", tz="UTC", inclusive="left")
    imp = pd.Series(30.0, index=idx)
    exp = pd.Series(13.0, index=idx)
    exp[idx.hour == 17] = 100.0  # an Axle-style export event
    model = _model(idx, imp, exp, initial_soc=20)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None

    flows = await model.flows(slots=await model.whole_horizon())

    assert (flows.loc[idx.hour < 17, "forced"] > 0).any(), "expected a charge before the event"
    assert (flows.loc[idx.hour == 17, "forced"] < 0).all(), "expected an export through the event"


@pytest.mark.asyncio
async def test_averaging_fills_idle_slots_in_a_same_price_period() -> None:
    # 2026-11-03: Whole Horizon charged 00:06-01:06, idled to 02:00, then charged on, all at 7.43p.
    idx = pd.date_range("2026-11-03 00:00", periods=4, freq="30min", tz="UTC")
    model = _model(idx, pd.Series(7.43, index=idx), 0.0, initial_soc=50, consumption=0.0)
    flows = await model.flows(slots=[])

    averaged = await model._average_same_price_runs(flows, [(idx[0], 3000.0), (idx[2], 3000.0)])

    assert averaged == [(start, 1500.0) for start in idx]


@pytest.mark.asyncio
async def test_whole_horizon_exports_late_when_the_price_is_flat() -> None:
    # 2026-11-04: at a flat 13p export Whole Horizon emptied the battery straight after the night charge;
    # the same export later keeps the energy in hand if the forecast is wrong.
    idx = pd.date_range("2026-11-05 07:00", periods=12, freq="30min", tz="UTC")
    model = _model(idx, pd.Series(32.87, index=idx), pd.Series(13.0, index=idx), initial_soc=70, consumption=757.0)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None

    plan = [(start, power) for start, power in await model.whole_horizon() if power < 0]

    assert plan, "expected an export"
    assert min(start for start, _ in plan) >= idx[6], plan  # 10:00; without the tie-break it was 07:00


@pytest.mark.asyncio
async def test_averaging_spreads_only_the_charge_the_battery_takes() -> None:
    # 2026-11-04: dropping an overnight export left the charge that refilled it, so the averaged
    # 00:00-07:00 charge filled the battery by 05:40 and "charged" a full battery to 07:00.
    idx = pd.date_range("2026-11-05 00:00", periods=4, freq="30min", tz="UTC")
    model = _model(idx, pd.Series(7.43, index=idx), 0.0, initial_soc=90, consumption=0.0)
    flows = await model.flows(slots=[])

    averaged = await model._average_same_price_runs(flows, [(start, 3000.0) for start in idx])
    soc = (await model.flows(slots=averaged))["soc_end"]

    assert len({p for _s, p in averaged}) == 1
    assert soc.iloc[-2] < 99.5 and soc.iloc[-1] > 99.5, list(soc)


@pytest.mark.asyncio
async def test_whole_horizon_idles_after_the_night_charge_and_exports_late() -> None:
    # 2026-11-05: Whole Horizon ran the night charge straight into a 07:00-13:00 export: one write fewer
    # than charge, idle, export, and the early-discharge cost was too small to pay for the extra write.
    idx = pd.date_range("2026-11-06 00:00", periods=48, freq="30min", tz="UTC")
    hour = idx.hour
    imp = pd.Series(32.87, index=idx)
    imp[hour < 7] = 7.43
    imp[(hour >= 16) & (hour < 20)] = 46.96
    load = pd.Series(500.0, index=idx)
    load[hour < 7] = 300.0
    load[hour >= 16] = 900.0
    model = _model(idx, imp, pd.Series(13.0, index=idx), initial_soc=15, consumption=load)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None

    flows = await model.flows(slots=await model.whole_horizon())
    exports = flows.index[flows["grid"] < -50]

    assert len(exports), "expected an export"
    assert exports[0].hour >= 20, list(exports)


@pytest.mark.asyncio
async def test_a_charge_in_progress_runs_to_the_end_of_the_cheap_window() -> None:
    # 2026-11-06: re-planning at 05:38 with the inverter charging, Whole Horizon assumed it was idle, so
    # continuing cost a write and stopping didn't. The charge stopped at 85% with cheap power until 07:00.
    idx = pd.date_range("2026-11-06 05:30", periods=40, freq="30min", tz="UTC")
    imp = pd.Series(32.87, index=idx)
    imp[idx.hour < 7] = 7.43
    model = _model(idx, imp, pd.Series(13.0, index=idx), initial_soc=90, consumption=757.0)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None
    model.whole_horizon_initial_control = "charge"

    flows = await model.flows(slots=await model.whole_horizon())

    assert flows["soc_end"].iloc[2] > 99, list(flows["soc_end"].iloc[:3])
