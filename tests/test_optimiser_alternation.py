"""Tests for optimiser control-slot alternation detection."""

import pandas as pd

from custom_components.miser.optimiser import _has_alternating_control_slots


def _slot(start: str, end: str, state: str) -> dict:
    return {
        "start": pd.Timestamp(start, tz="UTC"),
        "end": pd.Timestamp(end, tz="UTC"),
        "state": state,
        "power": 1000 if state == "charging" else -1000,
        "target_soc": 50,
    }


def test_single_charge_discharge_transition_is_allowed() -> None:
    assert not _has_alternating_control_slots(
        [
            _slot("2026-08-06 21:00", "2026-08-06 23:00", "discharging"),
            _slot("2026-08-06 23:00", "2026-08-07 05:30", "charging"),
        ]
    )


def test_repeated_adjacent_transitions_are_alternating() -> None:
    assert _has_alternating_control_slots(
        [
            _slot("2026-08-06 21:00", "2026-08-06 21:30", "charging"),
            _slot("2026-08-06 21:30", "2026-08-06 22:00", "discharging"),
            _slot("2026-08-06 22:00", "2026-08-06 22:30", "charging"),
        ]
    )


def test_repeated_transitions_with_large_gap_are_alternating() -> None:
    assert _has_alternating_control_slots(
        [
            _slot("2026-08-06 21:00", "2026-08-06 21:30", "charging"),
            _slot("2026-08-06 21:30", "2026-08-06 22:00", "discharging"),
            _slot("2026-08-07 02:00", "2026-08-07 05:30", "charging"),
        ]
    )
