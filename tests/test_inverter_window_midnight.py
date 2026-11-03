"""The Solis rejects windows that cross midnight or touch 00:00, so Miser writes 00:01-23:59 halves."""

import pandas as pd
import pytest
from homeassistant.util import dt as dt_util

from custom_components.miser.optimiser import _inverter_window


def _local(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=dt_util.DEFAULT_TIME_ZONE).tz_convert("UTC")


def _hm(window) -> tuple[str, str]:
    return tuple(t.tz_convert(dt_util.DEFAULT_TIME_ZONE).strftime("%d %H:%M") for t in window)


def test_window_crossing_midnight_is_written_in_two_halves() -> None:
    start, end = _local("2026-11-03 22:00"), _local("2026-11-04 02:00")

    assert _hm(_inverter_window(start, end, _local("2026-11-03 22:05"))) == ("03 22:00", "03 23:59")
    assert _hm(_inverter_window(start, end, _local("2026-11-04 00:01"))) == ("04 00:01", "04 02:00")


def test_window_starting_at_midnight_is_written_from_00_01_ahead_of_time() -> None:
    # 2026-11-02 23:56: a 00:00 start was due within the run interval.
    window = _inverter_window(_local("2026-11-04 00:00"), _local("2026-11-04 07:00"), _local("2026-11-03 23:56"))

    assert _hm(window) == ("04 00:01", "04 07:00")


def test_window_inside_one_day_is_unchanged() -> None:
    start, end = _local("2026-11-04 02:00"), _local("2026-11-04 07:00")

    assert _inverter_window(start, end, _local("2026-11-04 01:56")) == (start, end)


@pytest.mark.asyncio
@pytest.mark.parametrize("day_bounded", [True, False])
async def test_only_day_bounded_controllers_get_split_windows(day_bounded) -> None:
    # #17: the split is a Solis quirk, so it comes from the controller, not the optimiser.
    from custom_components.miser.optimiser import _programme_control_slot
    from tests.test_optimiser_control_windows import _FakeHass, _RecordingController

    controller = _RecordingController()
    controller.day_bounded_windows = day_bounded
    now = pd.Timestamp.now(tz="UTC")
    start = (now.tz_convert(dt_util.DEFAULT_TIME_ZONE).normalize() + pd.Timedelta(hours=23)).tz_convert("UTC")
    end = start + pd.Timedelta(hours=3)
    slot = {"start": start, "end": end, "state": "charging", "power": 3000.0, "target_soc": 90.0}

    assert await _programme_control_slot(_FakeHass(controller), controller, slot)

    written_end = pd.Timestamp(controller.charge_calls[0][1])
    assert (written_end < end) is day_bounded
