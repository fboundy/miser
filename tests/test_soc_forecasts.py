"""The SOC forecast sensors read the optimised plan's SOC a fixed time ahead."""

import pandas as pd

from custom_components.miser.optimiser import _predicted_soc

START = pd.Timestamp("2026-11-09 12:08", tz="UTC")
FLOWS = pd.DataFrame(
    {"soc": [50.0, 52.2, 60.0], "soc_end": [52.2, 60.0, 70.0], "dt_hours": [22 / 60, 0.5, 0.5]},
    index=[START, pd.Timestamp("2026-11-09 12:30", tz="UTC"), pd.Timestamp("2026-11-09 13:00", tz="UTC")],
)


def test_interpolates_within_a_slot() -> None:
    assert _predicted_soc(FLOWS, START + pd.Timedelta(minutes=37)) == 56.1  # 12:45, halfway through 12:30-13:00


def test_slot_end_is_the_next_slot_start() -> None:
    assert _predicted_soc(FLOWS, pd.Timestamp("2026-11-09 13:30", tz="UTC")) == 70.0


def test_beyond_the_plan_is_unknown() -> None:
    assert _predicted_soc(FLOWS, pd.Timestamp("2026-11-09 13:31", tz="UTC")) is None
    assert _predicted_soc(FLOWS, START - pd.Timedelta(minutes=1)) is None
