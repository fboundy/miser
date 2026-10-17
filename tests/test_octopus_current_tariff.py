"""Choosing the Octopus agreement actually in force."""

import pandas as pd

from custom_components.miser.octopus import current_tariff_code

# The real export meter point, as returned by the Octopus account API (Oct 2026).
EXPORT = [
    {"tariff_code": "E-1R-VAR-22-10-01-G", "valid_from": "2023-02-02T00:00:00Z", "valid_to": "2023-02-02T00:00:00Z"},
    {"tariff_code": "E-1R-AGILE-OUTGOING-19-05-13-G", "valid_from": "2023-02-02T00:00:00Z", "valid_to": "2023-03-13T00:00:00Z"},
    {"tariff_code": "E-1R-FLUX-EXPORT-23-02-14-G", "valid_from": "2023-03-13T00:00:00Z", "valid_to": "2023-09-21T00:00:00+01:00"},
    {"tariff_code": "E-1R-FLUX-EXPORT-23-02-14-G", "valid_from": "2023-09-21T00:00:00+01:00", "valid_to": "2023-09-21T00:00:00+01:00"},
    {"tariff_code": "E-1R-OUTGOING-FIX-12M-19-05-13-G", "valid_from": "2023-09-21T00:00:00+01:00", "valid_to": "2024-09-21T00:00:00+01:00"},
    {"tariff_code": "E-1R-OUTGOING-FIX-12M-19-05-13-G", "valid_from": "2024-09-21T00:00:00+01:00", "valid_to": "2025-09-21T00:00:00+01:00"},
    {"tariff_code": "E-1R-OUTGOING-VAR-24-10-26-G", "valid_from": "2025-09-21T00:00:00+01:00", "valid_to": None},
]

# The real import meter point, deliberately not in date order.
IMPORT = [
    {"tariff_code": "E-1R-AGILE-23-12-06-G", "valid_from": "2024-02-15T00:00:00Z", "valid_to": "2025-01-09T00:00:00Z"},
    {"tariff_code": "E-1R-GO-VAR-22-10-14-G", "valid_from": "2025-01-09T00:00:00Z", "valid_to": "2026-10-17T00:00:00+01:00"},
    {"tariff_code": "E-1R-AGILE-24-10-01-G", "valid_from": "2025-02-15T00:00:00Z", "valid_to": "2025-02-15T00:00:00Z"},
    {"tariff_code": "E-1R-FLUX-IMPORT-23-02-14-G", "valid_from": "2026-10-17T00:00:00+01:00", "valid_to": None},
]


def _at(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts, tz="UTC")


def test_real_accounts_pick_current_agreements() -> None:
    now = _at("2026-10-17 15:00")
    assert current_tariff_code(IMPORT, now) == "E-1R-FLUX-IMPORT-23-02-14-G"
    assert current_tariff_code(EXPORT, now) == "E-1R-OUTGOING-VAR-24-10-26-G"


def test_previous_agreement_used_until_switch_takes_effect() -> None:
    # The day before the Flux switch, Go was still in force.
    assert current_tariff_code(IMPORT, _at("2026-10-16 12:00")) == "E-1R-GO-VAR-22-10-14-G"


def test_future_dated_switch_is_not_used_early() -> None:
    booked = EXPORT + [
        {"tariff_code": "E-1R-FLUX-EXPORT-23-02-14-G", "valid_from": "2026-10-24T00:00:00+01:00", "valid_to": None},
    ]
    assert current_tariff_code(booked, _at("2026-10-17 15:00")) == "E-1R-OUTGOING-VAR-24-10-26-G"
    assert current_tariff_code(booked, _at("2026-10-24 12:00")) == "E-1R-FLUX-EXPORT-23-02-14-G"


def test_zero_length_agreement_sharing_a_start_is_ignored() -> None:
    # On 2023-09-21 a zero-length Flux Export and the real Outgoing Fix share a start.
    assert current_tariff_code(EXPORT, _at("2023-10-01 12:00")) == "E-1R-OUTGOING-FIX-12M-19-05-13-G"


def test_gap_falls_back_to_most_recently_started() -> None:
    gappy = [{"tariff_code": "OLD", "valid_from": "2025-01-01T00:00:00Z", "valid_to": "2025-06-01T00:00:00Z"}]
    assert current_tariff_code(gappy, _at("2025-07-01 00:00")) == "OLD"
    assert current_tariff_code([], _at("2025-07-01 00:00")) is None


import pytest

import homeassistant.util.dt as dt_util
from custom_components.miser.const import DOMAIN
from custom_components.miser.optimiser import TARIFF_REFRESH_INTERVAL, _refresh_tariffs_if_due


class _Hass:
    def __init__(self, checked_at) -> None:
        self.calls = 0

        async def refresher() -> None:
            self.calls += 1

        self.data = {DOMAIN: {"tariff_refresher": refresher, "tariffs_checked_at": checked_at}}


@pytest.mark.asyncio
async def test_refresh_skipped_when_recent() -> None:
    hass = _Hass(dt_util.utcnow())
    await _refresh_tariffs_if_due(hass)
    assert hass.calls == 0


@pytest.mark.asyncio
async def test_refresh_runs_when_due() -> None:
    hass = _Hass(dt_util.utcnow() - TARIFF_REFRESH_INTERVAL.to_pytimedelta() - dt_util.dt.timedelta(minutes=1))
    await _refresh_tariffs_if_due(hass)
    assert hass.calls == 1
