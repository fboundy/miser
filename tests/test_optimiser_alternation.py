"""Tests for optimiser rapid-alternation detection.

"Rapid" is the elapsed time across both transitions of an A -> B -> A reversal,
measured from the end of the first A window to the start of the second. That
span covers the middle window and both idle gaps, so a brief blip counts as
churn while an ordinary daily cycle does not.

The previous implementation counted bare state transitions with no notion of
time, which rejected the normal discharge/charge/discharge cycle as churn. On
the night of 2026-08-06 that behaviour discarded a plan earning 61p in favour of
one costing 15p, taking the overnight charge with it.
"""

import pandas as pd

from custom_components.miser.optimiser import (
    RAPID_ALTERNATION_HORIZON,
    _has_rapid_alternation,
)


def _window(start: str, end: str, state: str) -> dict:
    return {
        "start": pd.Timestamp(start, tz="UTC"),
        "end": pd.Timestamp(end, tz="UTC"),
        "state": state,
    }


def test_single_transition_is_not_alternation() -> None:
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 23:00", "discharging"),
            _window("2026-08-06 23:00", "2026-08-07 05:30", "charging"),
        ]
    )


def test_short_adjacent_reversal_is_rapid() -> None:
    """charge -> discharge -> charge inside an hour is inverter churn."""
    assert _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 21:30", "charging"),
            _window("2026-08-06 21:30", "2026-08-06 22:00", "discharging"),
            _window("2026-08-06 22:00", "2026-08-06 22:30", "charging"),
        ]
    )


def test_ordinary_daily_cycle_is_not_rapid() -> None:
    """The real shape this system runs: evening discharge, overnight charge,
    next evening discharge. Previously rejected as churn."""
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 17:00", "2026-08-06 21:00", "discharging"),
            _window("2026-08-06 23:30", "2026-08-07 04:30", "charging"),
            _window("2026-08-07 17:00", "2026-08-07 21:00", "discharging"),
        ]
    )


def test_long_middle_window_with_no_gaps_is_not_rapid() -> None:
    """Zero idle gaps, but the span across both transitions is five hours."""
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 18:00", "2026-08-06 21:00", "discharging"),
            _window("2026-08-06 21:00", "2026-08-07 02:00", "charging"),
            _window("2026-08-07 02:00", "2026-08-07 05:00", "discharging"),
        ]
    )


def test_short_middle_window_with_small_gaps_is_rapid() -> None:
    assert _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 21:30", "discharging"),
            _window("2026-08-06 21:40", "2026-08-06 21:55", "charging"),
            _window("2026-08-06 22:05", "2026-08-06 22:30", "discharging"),
        ]
    )


def test_short_middle_window_with_large_gaps_is_not_rapid() -> None:
    """A brief middle window is not churn if it is hours from its neighbours."""
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 21:30", "discharging"),
            _window("2026-08-07 00:00", "2026-08-07 00:15", "charging"),
            _window("2026-08-07 04:00", "2026-08-07 04:30", "discharging"),
        ]
    )


# --- horizon boundary: <, ==, > ---


def _triple_with_span(span: pd.Timedelta) -> list[dict]:
    """A -> B -> A where the end-of-first-A to start-of-second-A span is exact."""
    first_end = pd.Timestamp("2026-08-06 21:00", tz="UTC")
    second_start = first_end + span
    return [
        _window("2026-08-06 20:30", "2026-08-06 21:00", "charging"),
        {
            "start": first_end,
            "end": second_start,
            "state": "discharging",
        },
        {
            "start": second_start,
            "end": second_start + pd.Timedelta(minutes=30),
            "state": "charging",
        },
    ]


def test_span_just_under_horizon_is_rapid() -> None:
    assert _has_rapid_alternation(
        _triple_with_span(RAPID_ALTERNATION_HORIZON - pd.Timedelta(minutes=1))
    )


def test_span_exactly_at_horizon_is_rapid() -> None:
    assert _has_rapid_alternation(_triple_with_span(RAPID_ALTERNATION_HORIZON))


def test_span_just_over_horizon_is_not_rapid() -> None:
    assert not _has_rapid_alternation(
        _triple_with_span(RAPID_ALTERNATION_HORIZON + pd.Timedelta(minutes=1))
    )


# --- shape guards ---


def test_two_windows_are_never_rapid() -> None:
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 21:05", "charging"),
            _window("2026-08-06 21:05", "2026-08-06 21:10", "discharging"),
        ]
    )


def test_same_direction_run_is_not_alternation() -> None:
    """Three charge windows in quick succession are not a reversal."""
    assert not _has_rapid_alternation(
        [
            _window("2026-08-06 21:00", "2026-08-06 21:10", "charging"),
            _window("2026-08-06 21:15", "2026-08-06 21:25", "charging"),
            _window("2026-08-06 21:30", "2026-08-06 21:40", "charging"),
        ]
    )


def test_unordered_input_is_sorted_before_evaluation() -> None:
    windows = [
        _window("2026-08-06 22:00", "2026-08-06 22:30", "charging"),
        _window("2026-08-06 21:00", "2026-08-06 21:30", "charging"),
        _window("2026-08-06 21:30", "2026-08-06 22:00", "discharging"),
    ]
    assert _has_rapid_alternation(windows)


def test_rapid_cluster_later_in_a_long_plan_is_found() -> None:
    """A clean daily cycle followed by genuine churn must still be flagged."""
    assert _has_rapid_alternation(
        [
            _window("2026-08-06 17:00", "2026-08-06 21:00", "discharging"),
            _window("2026-08-06 23:30", "2026-08-07 04:30", "charging"),
            _window("2026-08-07 12:00", "2026-08-07 12:15", "discharging"),
            _window("2026-08-07 12:20", "2026-08-07 12:35", "charging"),
            _window("2026-08-07 12:40", "2026-08-07 12:55", "discharging"),
        ]
    )
