"""Tests for Axle VPP callback refresh handling."""

from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock

import homeassistant.util.dt as dt_util
import pytest

import custom_components.miser as miser_init
from custom_components.miser.const import DOMAIN
from custom_components.miser.optimiser import AXLE_VPP_END_ENTITIES, AXLE_VPP_START_ENTITIES


class _ConfigEntries:
    def __init__(self, installed: bool):
        self.installed = installed

    def async_entries(self, domain):
        return [SimpleNamespace(domain=domain)] if self.installed else []


class _States:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, entity_id):
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=value)


def _hass(installed=True, states=None):
    return SimpleNamespace(
        data={DOMAIN: {}},
        config_entries=_ConfigEntries(installed),
        states=_States(states),
        async_create_task=lambda coro: coro,
    )


@pytest.mark.asyncio
async def test_refresh_registers_axle_callbacks_after_late_install(monkeypatch):
    """Test the refresh path registers Axle callbacks when Axle appears after setup."""
    registered = []

    def _track_state_change(hass, entity_id, callback):
        registered.append((entity_id, callback))
        return lambda: None

    monkeypatch.setattr(miser_init, "async_track_state_change", _track_state_change)

    hass = _hass(installed=True)

    await miser_init._refresh_axle_vpp_callbacks(hass)

    assert hass.data[DOMAIN]["axle_vpp_installed"] is True
    assert [entity_id for entity_id, _callback in registered] == AXLE_VPP_START_ENTITIES + AXLE_VPP_END_ENTITIES
    assert len(hass.data[DOMAIN]["axle_vpp_callbacks"]) == 4


@pytest.mark.asyncio
async def test_refresh_rechecks_control_when_window_is_discovered_inside_buffer(monkeypatch):
    """Test a periodic refresh reacts when an Axle event is already inside its buffer."""
    now = dt_util.utcnow()
    start = now + timedelta(minutes=1)
    end = now + timedelta(minutes=11)
    states = {
        AXLE_VPP_START_ENTITIES[0]: start.isoformat(),
        AXLE_VPP_END_ENTITIES[0]: end.isoformat(),
    }
    hass = _hass(installed=True, states=states)
    optimise = AsyncMock()

    monkeypatch.setattr(miser_init, "optimise", optimise)
    monkeypatch.setattr(miser_init, "async_track_state_change", lambda *args: (lambda: None))
    monkeypatch.setattr(miser_init, "async_track_point_in_utc_time", lambda *args: (lambda: None))

    await miser_init._refresh_axle_vpp_callbacks(hass, optimise_on_window_change=True)

    assert hass.data[DOMAIN]["axle_vpp_window_key"]
    optimise.assert_awaited_once_with(hass=hass)
