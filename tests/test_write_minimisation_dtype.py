"""The write-minimisation candidates must accept a plan whose powers are all whole numbers."""

import pandas as pd

from custom_components.miser.optimiser import _minimise_inverter_write_slot_candidates


def test_whole_number_plan_can_be_respread():
    # Two exports at the same price and different powers: respreading gives a fractional power,
    # which an int64 column rejected (TypeError, the whole run lost).
    index = pd.date_range("2026-11-06 22:00", periods=4, freq="30min", tz="UTC")
    flows = pd.DataFrame(
        {"forced": [-3031, -2910, 0, 0], "export": 13.0, "import": 32.87, "dt_hours": 0.5, "soc_end": [60.0, 55.0, 55.0, 55.0]},
        index=index,
    )
    assert flows["forced"].dtype == "int64"
    for _label, slots in _minimise_inverter_write_slot_candidates(flows):
        assert all(isinstance(p, float) for _, p in slots)
