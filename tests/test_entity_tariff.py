"""Prices read from Home Assistant entities."""

import pandas as pd
import pytest

from custom_components.miser.entity_tariff import (
    PRICE_UNIT_GBP,
    PRICE_UNIT_PENCE,
    EntityTariff,
)


class _State:
    def __init__(self, state, attributes=None) -> None:
        self.state = state
        self.attributes = attributes or {}


class _States:
    def __init__(self, states: dict) -> None:
        self._states = states

    def get(self, entity_id):
        return self._states.get(entity_id)


class _Hass:
    def __init__(self, states: dict) -> None:
        self.states = _States(states)


def _day_rates(day: str, prices_gbp: dict[str, float], base: float) -> list[dict]:
    """Octopus Energy integration shape: 48 half-hours, value_inc_vat in GBP/kWh."""
    rates = []
    for slot in pd.date_range(f"{day} 00:00", periods=48, freq="30min", tz="Europe/London"):
        value = base
        for hhmm, price in prices_gbp.items():
            start, end = hhmm.split("-")
            if start <= slot.strftime("%H:%M") < end:
                value = price
        rates.append(
            {
                "start": slot.isoformat(),
                "end": (slot + pd.Timedelta("30min")).isoformat(),
                "value_inc_vat": value,
                "is_capped": False,
            }
        )
    return rates


FLUX = {"02:00-05:00": 0.161015, "16:00-19:00": 0.372347}
CURRENT = "event.octopus_energy_electricity_x_current_day_rates"
NEXT = "event.octopus_energy_electricity_x_next_day_rates"


def _flux_hass() -> _Hass:
    return _Hass(
        {
            CURRENT: _State("2026-10-19T00:00:00+00:00", {"rates": _day_rates("2026-10-19", FLUX, 0.266681)}),
            NEXT: _State("2026-10-19T16:00:00+00:00", {"rates": _day_rates("2026-10-20", FLUX, 0.266681)}),
        }
    )


@pytest.mark.asyncio
async def test_octopus_energy_rates_are_converted_to_pence() -> None:
    tariff = EntityTariff(_flux_hass(), [CURRENT, NEXT])
    df = await tariff.to_df(
        start=pd.Timestamp("2026-10-19 12:00", tz="UTC"), end=pd.Timestamp("2026-10-20 23:30", tz="UTC")
    )

    # 16:00-19:00 BST = 15:00-18:00 UTC peak; 02:00-05:00 BST = 01:00-04:00 UTC cheap.
    assert round(df.loc[pd.Timestamp("2026-10-19 15:30", tz="UTC"), "unit"], 2) == 37.23
    assert round(df.loc[pd.Timestamp("2026-10-20 02:00", tz="UTC"), "unit"], 2) == 16.10
    assert round(df.loc[pd.Timestamp("2026-10-20 10:00", tz="UTC"), "unit"], 2) == 26.67
    assert "fixed" in df.columns and (df["fixed"] == 0).all()


@pytest.mark.asyncio
async def test_prices_beyond_published_repeat_the_last_day() -> None:
    tariff = EntityTariff(_flux_hass(), [CURRENT, NEXT])
    df = await tariff.to_df(
        start=pd.Timestamp("2026-10-19 12:00", tz="UTC"), end=pd.Timestamp("2026-10-22 12:00", tz="UTC")
    )
    assert not df["unit"].isna().any()
    # 21 Oct 15:30 UTC was never published; it repeats 20 Oct's peak.
    assert round(df.loc[pd.Timestamp("2026-10-21 15:30", tz="UTC"), "unit"], 2) == 37.23


@pytest.mark.asyncio
async def test_flat_state_is_used_as_a_constant_price() -> None:
    hass = _Hass({"sensor.export_rate": _State("0.12", {"unit_of_measurement": "GBP/kWh"})})
    tariff = EntityTariff(hass, ["sensor.export_rate"], export=True)
    df = await tariff.to_df(start=pd.Timestamp("2026-10-19 12:00", tz="UTC"), end=pd.Timestamp("2026-10-20 12:00", tz="UTC"))
    assert set(df["unit"].round(2)) == {12.0}
    assert "fixed" not in df.columns


@pytest.mark.asyncio
async def test_pence_unit_is_not_scaled() -> None:
    hass = _Hass({"sensor.price": _State("26.67", {"unit_of_measurement": "p/kWh"})})
    df = await EntityTariff(hass, ["sensor.price"]).to_df(
        start=pd.Timestamp("2026-10-19 12:00", tz="UTC"), end=pd.Timestamp("2026-10-19 13:00", tz="UTC")
    )
    assert set(df["unit"].round(2)) == {26.67}


def test_explicit_unit_overrides_detection() -> None:
    hass = _Hass({"sensor.price": _State("1.5")})  # would auto-detect as GBP
    assert EntityTariff(hass, ["sensor.price"], price_unit=PRICE_UNIT_PENCE).read_rates()[1] == 1.5
    assert EntityTariff(hass, ["sensor.price"], price_unit=PRICE_UNIT_GBP).read_rates()[1] == 150.0


@pytest.mark.asyncio
async def test_nordpool_raw_today_and_tomorrow() -> None:
    def raw(day, value):
        return [
            {"start": t.isoformat(), "end": (t + pd.Timedelta("1h")).isoformat(), "value": value}
            for t in pd.date_range(f"{day} 00:00", periods=24, freq="1h", tz="UTC")
        ]

    hass = _Hass(
        {"sensor.nordpool": _State("20.0", {"unit_of_measurement": "p/kWh", "raw_today": raw("2026-10-19", 20.0), "raw_tomorrow": raw("2026-10-20", 30.0)})}
    )
    df = await EntityTariff(hass, ["sensor.nordpool"]).to_df(
        start=pd.Timestamp("2026-10-19 22:00", tz="UTC"), end=pd.Timestamp("2026-10-20 02:00", tz="UTC")
    )
    assert df.loc[pd.Timestamp("2026-10-19 23:30", tz="UTC"), "unit"] == 20.0
    assert df.loc[pd.Timestamp("2026-10-20 01:30", tz="UTC"), "unit"] == 30.0


@pytest.mark.asyncio
async def test_missing_entities_raise() -> None:
    with pytest.raises(ValueError):
        await EntityTariff(_Hass({}), ["sensor.nope"]).to_df(
            start=pd.Timestamp("2026-10-19 12:00", tz="UTC"), end=pd.Timestamp("2026-10-19 13:00", tz="UTC")
        )


def test_tariff_code_identifies_the_entities() -> None:
    assert EntityTariff(_Hass({}), [CURRENT, NEXT]).tariff_code == f"entities:{CURRENT},{NEXT}"
