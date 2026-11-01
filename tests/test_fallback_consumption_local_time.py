"""The fallback load shape is in local clock time (audit 16)."""

from unittest.mock import AsyncMock

import homeassistant.util.dt as dt_util
import pandas as pd
import pytest

from custom_components.miser import optimiser
from custom_components.miser.const import CONF_DAILY_CONSUMPTION_KWH, CONF_SHAPE_CONSUMPTION


@pytest.mark.asyncio
async def test_shape_follows_local_time(monkeypatch) -> None:
    values = {CONF_DAILY_CONSUMPTION_KWH: 12, CONF_SHAPE_CONSUMPTION: True}
    monkeypatch.setattr(optimiser, "get_value", AsyncMock(side_effect=lambda hass, key, **kw: values[key]))
    index = pd.date_range("2026-07-01 00:00", periods=48, freq="30min", tz="UTC")

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/London"))
    try:
        fallback = await optimiser._fallback_consumption(None, pd.DataFrame(index=index))
    finally:
        dt_util.set_default_time_zone(original)

    # 16:00 UTC is 17:00 BST, the start of the evening peak; 15:00 UTC (16:00 BST) is before it.
    assert fallback[pd.Timestamp("2026-07-01 16:00", tz="UTC")] > fallback[pd.Timestamp("2026-07-01 14:00", tz="UTC")]
    assert fallback[pd.Timestamp("2026-07-01 16:00", tz="UTC")] == fallback[pd.Timestamp("2026-07-01 20:00", tz="UTC")]
    assert not fallback.isna().any()
