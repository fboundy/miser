"""Tests for Axle VPP price handling."""

from types import SimpleNamespace

import pandas as pd

from custom_components.miser.optimiser import (
    AXLE_VPP_EXPORT_PRICE,
    AXLE_VPP_END_ENTITIES,
    AXLE_VPP_START_ENTITIES,
    _apply_axle_vpp_export_price,
)


class _ConfigEntries:
    def async_entries(self, domain):
        return [SimpleNamespace(domain=domain)]


class _States:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=value)


def _hass_with_axle_session(start: str, end: str):
    return SimpleNamespace(
        config_entries=_ConfigEntries(),
        states=_States(
            {
                AXLE_VPP_START_ENTITIES[0]: start,
                AXLE_VPP_END_ENTITIES[0]: end,
            }
        ),
    )


def test_apply_axle_vpp_export_price_updates_overlapping_session_slots():
    """Test Axle event slots are valued at the Axle export price."""
    prices = pd.DataFrame(
        {"import": 20.0, "export": 15.0},
        index=pd.date_range("2099-07-20 00:00", periods=6, freq="30min", tz="UTC"),
    )
    hass = _hass_with_axle_session("2099-07-20T00:30:00+00:00", "2099-07-20T01:30:00+00:00")

    _apply_axle_vpp_export_price(hass, prices, pd.Timedelta(minutes=30))

    assert prices["export"].tolist() == [
        15.0,
        AXLE_VPP_EXPORT_PRICE,
        AXLE_VPP_EXPORT_PRICE,
        15.0,
        15.0,
        15.0,
    ]


def test_apply_axle_vpp_export_price_does_not_update_buffer_only_slots():
    """Test the cede-control buffer is not priced as an Axle export session."""
    prices = pd.DataFrame(
        {"import": 20.0, "export": 15.0},
        index=pd.date_range("2099-07-20 00:00", periods=4, freq="30min", tz="UTC"),
    )
    hass = _hass_with_axle_session("2099-07-20T00:31:00+00:00", "2099-07-20T00:59:00+00:00")

    _apply_axle_vpp_export_price(hass, prices, pd.Timedelta(minutes=30))

    assert prices["export"].tolist() == [
        15.0,
        AXLE_VPP_EXPORT_PRICE,
        15.0,
        15.0,
    ]
