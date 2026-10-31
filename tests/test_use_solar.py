"""The Use Solar switch turns the solar forecast off."""

from types import SimpleNamespace

import pandas as pd
import pytest

from custom_components.miser import optimiser
from custom_components.miser.const import CONF_USE_SOLAR, DOMAIN


@pytest.mark.asyncio
async def test_use_solar_off_gives_zero_solar(monkeypatch) -> None:
    async def get_value(hass, key, default_value=None):
        return False if key == CONF_USE_SOLAR else default_value

    monkeypatch.setattr(optimiser, "get_value", get_value)
    model = SimpleNamespace(solar=None)
    # A Solcast entry exists, but must not be read.
    hass = SimpleNamespace(
        data={DOMAIN: {"model": model}},
        config_entries=SimpleNamespace(async_entries=lambda domain: [object()]),
    )
    start = pd.Timestamp("2026-10-31 12:00", tz="UTC")
    await optimiser._get_solcast(hass, start, start + pd.Timedelta(hours=2), pd.Timedelta(minutes=30))

    assert len(model.solar) == 4
    assert (model.solar == 0).all()
