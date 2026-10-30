"""The charge/discharge limits are battery-side; forced power is grid-side.

The model must allow enough grid-side forced power to drive the battery to its
limit, and the commanded battery power (forced x charger efficiency, or forced
/ inverter efficiency) must then land exactly on that limit.
"""

import pandas as pd

from custom_components.miser.optimiser import _battery_side_power
from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model(inverter_power: int = 3600) -> PVsystemModel:
    return PVsystemModel(
        inverter=InverterModel(
            inverter_efficiency=97,
            charger_efficiency=91,
            inverter_power=inverter_power,
            charger_power=3000,
        ),
        battery=BatteryModel(
            battery_capacity=20000,
            battery_minimum_soc=15,
            battery_current_limit=60,  # 60 A x 50 V = 3000 W
            voltage=50,
        ),
    )


def test_battery_limits_are_the_dc_figures() -> None:
    model = _model()
    assert model.battery_charge_limit == 3000
    assert model.battery_discharge_limit == 3000


def test_grid_side_charge_limit_drives_battery_to_its_limit() -> None:
    model = _model()
    assert round(model.charge_power_limit, 3) == round(3000 / 0.91, 3)
    commanded = _battery_side_power(model, "charging", model.charge_power_limit)
    assert round(commanded, 6) == 3000


def test_grid_side_discharge_limit_drives_battery_to_its_limit() -> None:
    model = _model()
    assert round(model.discharge_power_limit, 3) == round(3000 * 0.97, 3)
    commanded = _battery_side_power(model, "discharging", -model.discharge_power_limit)
    assert round(commanded, 6) == 3000


def test_discharge_limit_still_capped_by_inverter_ac_rating() -> None:
    model = _model(inverter_power=2500)
    assert model.discharge_power_limit == 2500


def test_forced_charge_at_limit_puts_full_rate_into_battery() -> None:
    """In the flows model, a forced charge at the grid-side limit adds the
    battery limit x dt to stored energy (not limit x efficiency)."""
    model = _model()
    idx = pd.date_range("2026-10-03 23:30", periods=2, freq="30min", tz="UTC")
    model.solar = pd.Series([0.0, 0.0], index=idx, name="solar")
    model.consumption = pd.Series([0.0, 0.0], index=idx, name="consumption")
    model.prices = pd.DataFrame({"import": 8.6, "export": 15.0}, index=idx)
    model.initial_soc = 20

    import asyncio

    flows = asyncio.run(model.flows(slots=[(idx[0], model.charge_power_limit)]))
    gained_wh = flows["chg_end"].iloc[0] - flows["chg"].iloc[0]
    assert round(gained_wh, 0) == round(3000 * 0.5, 0)
