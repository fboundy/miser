"""Octopus API tariffs: validity periods, refresh, and gap filling."""

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from custom_components.miser import octopus
from custom_components.miser.octopus import Tariff, rates_to_series


def _rate(value, valid_from, valid_to=None, payment="DIRECT_DEBIT"):
    return {"value_inc_vat": value, "valid_from": valid_from, "valid_to": valid_to, "payment_method": payment}


def _idx(start, end):
    return pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="30min")


# The real Outgoing history: 15p until 1 Mar 2026, then 12p open-ended.
OUTGOING = [
    _rate(15.0, "2024-10-25T23:00:00Z", "2026-03-01T00:00:00Z"),
    _rate(12.0, "2026-03-01T00:00:00Z", None),
]


def test_superseded_rate_does_not_leak_past_its_end() -> None:
    """Regression: Miser used 15p export for seven months after it fell to 12p."""
    series = rates_to_series(OUTGOING, _idx("2026-10-19 00:00", "2026-10-21 00:00"))
    assert set(series) == {12.0}


def test_rate_change_inside_the_horizon() -> None:
    series = rates_to_series(OUTGOING, _idx("2026-02-28 22:00", "2026-03-01 02:00"))
    assert series[pd.Timestamp("2026-02-28 23:30", tz="UTC")] == 15.0
    assert series[pd.Timestamp("2026-03-01 00:00", tz="UTC")] == 12.0


def _flux_day(day: str, cheap: float, normal: float, peak: float) -> list[dict]:
    """Flux publishes per-block rows each day (times in UTC, BST in force)."""
    d = pd.Timestamp(day, tz="UTC")
    blocks = [("00:00", "01:00", normal), ("01:00", "04:00", cheap), ("04:00", "15:00", normal),
              ("15:00", "18:00", peak), ("18:00", "24:00", normal)]
    return [_rate(v, (d + pd.Timedelta(a + ":00")).isoformat(), (d + pd.Timedelta(b + ":00")).isoformat()) for a, b, v in blocks]


@pytest.mark.asyncio
async def test_flux_prices_follow_published_rows_then_repeat() -> None:
    tariff = Tariff("E-1R-FLUX-IMPORT-23-02-14-G")
    tariff.unit = _flux_day("2026-10-18", 14.45, 24.07, 33.70) + _flux_day("2026-10-19", 16.10, 26.67, 37.23)
    tariff.fixed = [_rate(44.85, "2026-10-01T00:00:00Z")]
    tariff._fetched_at = pd.Timestamp.now(tz="UTC")  # skip the network

    df = await tariff.to_df(start=pd.Timestamp("2026-10-18 12:00", tz="UTC"), end=pd.Timestamp("2026-10-20 23:30", tz="UTC"))

    assert df.loc[pd.Timestamp("2026-10-18 16:00", tz="UTC"), "unit"] == 33.70  # old peak
    assert df.loc[pd.Timestamp("2026-10-19 16:00", tz="UTC"), "unit"] == 37.23  # new peak
    # 20 Oct is unpublished: repeats 19 Oct.
    assert df.loc[pd.Timestamp("2026-10-20 02:00", tz="UTC"), "unit"] == 16.10
    assert df.loc[pd.Timestamp("2026-10-20 16:00", tz="UTC"), "unit"] == 37.23
    assert not df["unit"].isna().any()
    # Standing charge only at midnight.
    assert df.loc[pd.Timestamp("2026-10-19 00:00", tz="UTC"), "fixed"] == 44.85
    assert df.loc[pd.Timestamp("2026-10-19 12:00", tz="UTC"), "fixed"] == 0


@pytest.mark.asyncio
async def test_economy7_uses_night_rate_in_night_window() -> None:
    tariff = Tariff("E-2R-VAR-22-10-01-G")
    tariff.day = [_rate(30.0, "2026-01-01T00:00:00Z")]
    tariff.night = [_rate(12.0, "2026-01-01T00:00:00Z")]
    tariff.unit = tariff.day
    tariff.fixed = [_rate(50.0, "2026-01-01T00:00:00Z")]
    tariff._fetched_at = pd.Timestamp.now(tz="UTC")

    df = await tariff.to_df(start=pd.Timestamp("2026-10-19 00:00", tz="UTC"), end=pd.Timestamp("2026-10-19 12:00", tz="UTC"))
    assert df.loc[pd.Timestamp("2026-10-19 02:00", tz="UTC"), "unit"] == 12.0  # 01:00-08:00 UTC
    assert df.loc[pd.Timestamp("2026-10-19 09:00", tz="UTC"), "unit"] == 30.0


@pytest.mark.asyncio
async def test_rates_refresh_when_stale(monkeypatch) -> None:
    tariff = Tariff("E-1R-OUTGOING-VAR-24-10-26-G", export=True)
    tariff.unit = [_rate(15.0, "2024-10-25T23:00:00Z")]
    tariff._fetched_at = pd.Timestamp.now(tz="UTC") - octopus.RATE_REFRESH_INTERVAL - pd.Timedelta(minutes=1)

    async def fetch(self, *args, **kwargs):
        self.unit = OUTGOING
        self._fetched_at = pd.Timestamp.now(tz="UTC")

    monkeypatch.setattr(Tariff, "_get_from_api", fetch)
    df = await tariff.to_df(start=pd.Timestamp("2026-10-19 00:00", tz="UTC"), end=pd.Timestamp("2026-10-19 02:00", tz="UTC"))
    assert set(df["unit"]) == {12.0}


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_rates(monkeypatch) -> None:
    tariff = Tariff("E-1R-OUTGOING-VAR-24-10-26-G", export=True)
    tariff.unit = OUTGOING
    tariff._fetched_at = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=5)
    monkeypatch.setattr(Tariff, "_get_from_api", AsyncMock(side_effect=OSError("API down")))

    df = await tariff.to_df(start=pd.Timestamp("2026-10-19 00:00", tz="UTC"), end=pd.Timestamp("2026-10-19 02:00", tz="UTC"))
    assert set(df["unit"]) == {12.0}


@pytest.mark.asyncio
async def test_first_fetch_failure_is_raised(monkeypatch) -> None:
    monkeypatch.setattr(Tariff, "_get_from_api", AsyncMock(side_effect=OSError("API down")))
    with pytest.raises(OSError):
        await Tariff("E-1R-OUTGOING-VAR-24-10-26-G", export=True).to_df(
            start=pd.Timestamp("2026-10-19 00:00", tz="UTC"), end=pd.Timestamp("2026-10-19 02:00", tz="UTC")
        )


def test_non_direct_debit_duplicates_are_dropped() -> None:
    rates = octopus._usable_rates([_rate(20.0, "2026-01-01T00:00:00Z"), _rate(21.0, "2026-01-01T00:00:00Z", payment="NON_DIRECT_DEBIT"), _rate(19.0, "2026-01-01T00:00:00Z", payment=None)])
    assert sorted(r["value_inc_vat"] for r in rates) == [19.0, 20.0]
