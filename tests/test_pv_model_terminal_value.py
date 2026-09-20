"""Terminal end-of-horizon SoC valuation, symmetric replacement-cost form.

Ending below the run-start SoC is penalised at the cheapest forward-window
import price; ending above it is credited the same way, but only when
value_surplus_soc is on. With the toggle off the behaviour is deficit-only.
"""

import pandas as pd

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel

CAPACITY = 10000  # Wh
START_SOC = 50    # %
TARGET_WH = CAPACITY * START_SOC / 100  # 5000 Wh


def _model(value_surplus: bool, forward_import: list[float]) -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=100, charger_efficiency=100),
        battery=BatteryModel(
            battery_capacity=CAPACITY,
            battery_minimum_soc=10,
            battery_current_limit=100,
            voltage=50,
        ),
    )
    model.initial_soc = START_SOC
    idx = pd.date_range("2026-09-21 00:00", periods=len(forward_import), freq="30min", tz="UTC")
    model.valuation_prices = pd.DataFrame({"import": forward_import}, index=idx)
    model.value_surplus_soc = value_surplus
    return model


def test_deficit_is_penalised_regardless_of_toggle() -> None:
    # 1000 Wh below target, cheapest forward import 10p/kWh -> 0.1 kWh * 10p = 10p... wait
    # 1000 Wh = 1 kWh at 10p/kWh = 10p penalty (lossless charger).
    for surplus in (True, False):
        model = _model(surplus, [10.0, 30.0])
        assert round(model.terminal_soc_value(TARGET_WH - 1000), 3) == 10.0


def test_surplus_is_credited_only_when_enabled() -> None:
    on = _model(True, [10.0, 30.0])
    off = _model(False, [10.0, 30.0])
    # 1000 Wh above target, valued at the cheapest forward slot (10p/kWh) -> -10p credit.
    assert round(on.terminal_soc_value(TARGET_WH + 1000), 3) == -10.0
    assert off.terminal_soc_value(TARGET_WH + 1000) == 0.0


def test_deficit_and_surplus_are_symmetric_in_magnitude() -> None:
    model = _model(True, [10.0, 30.0])
    penalty = model.terminal_soc_value(TARGET_WH - 1500)
    credit = model.terminal_soc_value(TARGET_WH + 1500)
    assert penalty > 0 and credit < 0
    assert round(penalty, 6) == round(-credit, 6)


def test_at_target_is_zero() -> None:
    model = _model(True, [10.0, 30.0])
    assert model.terminal_soc_value(TARGET_WH) == 0.0


def test_surplus_uses_cheapest_forward_price_first() -> None:
    # charge_power_limit = min(battery 100 A * 50 V = 5000 W, inverter 3500 W) = 3500 W,
    # so one 0.5 h forward slot absorbs 1750 Wh.
    model = _model(True, [8.0, 40.0])
    # 1750 Wh surplus fits entirely in the cheapest 8p slot -> 1.75 kWh * 8p = 14p.
    assert round(model.terminal_soc_value(TARGET_WH + 1750), 3) == -14.0
    # 2000 Wh spills 250 Wh into the 40p slot: 14p + 0.25 kWh * 40p = 14 + 10 = 24p.
    assert round(model.terminal_soc_value(TARGET_WH + 2000), 3) == -24.0


def test_no_valuation_prices_means_no_adjustment() -> None:
    model = _model(True, [10.0])
    model.valuation_prices = pd.DataFrame()
    assert model.terminal_soc_value(TARGET_WH + 2000) == 0.0
    assert model.terminal_soc_value(TARGET_WH - 2000) == 0.0
