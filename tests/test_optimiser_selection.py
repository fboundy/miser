"""Tests for bounded optimiser plan selection.

The selector may substitute a non-alternating plan for a rapidly alternating
one, but only when the substitution costs no more than the configured raw
financial threshold. Previously the substitution was unbounded: on 2026-08-07 it
repeatedly replaced a plan earning 23.6p with one costing 52.7p, a 76p swing,
and because the substitute carried no charging slots it removed the overnight
charge entirely.
"""

import pandas as pd
import pytest

from custom_components.miser.const import COST_ENTITY_OBJECTS, DEFAULTS, DOMAIN
from custom_components.miser.const import CONF_ALTERNATION_COST_THRESHOLD
from custom_components.miser.optimiser import (
    _control_windows,
    _select_optimised_key,
)


def _flows(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(start, tz="UTC") for start, _, _ in rows])
    return pd.DataFrame(
        {
            "forced": [power for _, power, _ in rows],
            "soc_end": [soc for _, _, soc in rows],
            "dt_hours": [0.5] * len(rows),
        },
        index=index,
    )


def _rapid_flows() -> pd.DataFrame:
    """charge -> discharge -> charge within the hour: genuine churn."""
    return _flows(
        [
            ("2026-08-06 21:00", 3000.0, 25.0),
            ("2026-08-06 21:30", -3000.0, 18.0),
            ("2026-08-06 22:00", 3000.0, 25.0),
        ]
    )


def _calm_flows() -> pd.DataFrame:
    """A single uninterrupted charge window."""
    return _flows(
        [
            ("2026-08-06 23:30", 3000.0, 22.0),
            ("2026-08-07 00:00", 3000.0, 29.0),
        ]
    )


class _FakeStates:
    def __init__(self, threshold=None):
        self._threshold = threshold

    def get(self, entity_id):
        if self._threshold is None:
            return None
        return type("S", (), {"state": str(self._threshold)})()


class _FakeConfigEntries:
    def async_entries(self, domain):
        return []


class _FakeHass:
    def __init__(self, threshold=None):
        self.data = {
            DOMAIN: {
                COST_ENTITY_OBJECTS: {},
                "model_entities": {},
                "control_entities": {},
                "config_entities": (
                    {CONF_ALTERNATION_COST_THRESHOLD: "number.miser_alternation_cost_threshold"}
                    if threshold is not None
                    else {}
                ),
            }
        }
        self.states = _FakeStates(threshold)
        # get_value falls back to the entity registry when no mapping exists;
        # returning no entries makes it use the configured default instead.
        self.config_entries = _FakeConfigEntries()


class _FakeModel:
    """Carries <key>_cost and <key>_flows attributes like the real model."""

    def __init__(self, **plans):
        for name, (cost, flows) in plans.items():
            setattr(self, f"{name}_cost", cost)
            setattr(self, f"{name}_flows", flows)


def test_rapid_detection_sees_the_churn_fixture() -> None:
    assert len(_control_windows(_rapid_flows())) == 3
    assert len(_control_windows(_calm_flows())) == 1


@pytest.mark.asyncio
async def test_cheapest_plan_wins_when_not_alternating() -> None:
    model = _FakeModel(
        whole_horizon=(-61.1, _calm_flows()),
        discharge=(15.1, _calm_flows()),
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == "whole_horizon_cost"


@pytest.mark.asyncio
async def test_substitution_happens_when_within_threshold() -> None:
    model = _FakeModel(
        whole_horizon=(-61.1, _rapid_flows()),
        discharge=(-55.0, _calm_flows()),  # 6.1p worse, within the 10p default
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == "discharge_cost"


@pytest.mark.asyncio
async def test_the_incident_substitution_is_now_refused() -> None:
    """The exact 2026-08-07 numbers: -23.6p must not become +52.7p."""
    model = _FakeModel(
        whole_horizon=(-23.6, _rapid_flows()),
        discharge=(52.7, _calm_flows()),
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == "whole_horizon_cost"


@pytest.mark.asyncio
async def test_safe_candidate_cheaper_than_winner_is_taken() -> None:
    """A negative delta is always within threshold."""
    model = _FakeModel(
        whole_horizon=(10.0, _rapid_flows()),
        discharge=(-5.0, _calm_flows()),
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == "discharge_cost"


@pytest.mark.asyncio
async def test_no_safe_candidate_keeps_the_winner() -> None:
    model = _FakeModel(
        whole_horizon=(-61.1, _rapid_flows()),
        discharge=(15.1, _rapid_flows()),
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == "whole_horizon_cost"


# --- threshold boundary: below, exactly at, just over ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0.0, "discharge_cost"),
        (9.9, "discharge_cost"),
        (10.0, "discharge_cost"),   # exactly at threshold: permitted
        (10.1, "whole_horizon_cost"),  # just over: refused
    ],
)
async def test_threshold_boundary(delta: float, expected: str) -> None:
    winner_cost = -20.0
    model = _FakeModel(
        whole_horizon=(winner_cost, _rapid_flows()),
        discharge=(winner_cost + delta, _calm_flows()),
    )
    key = await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])
    assert key == expected


@pytest.mark.asyncio
async def test_configured_threshold_is_honoured() -> None:
    """A 40p threshold permits a swap the 10p default would refuse."""
    model = _FakeModel(
        whole_horizon=(-20.0, _rapid_flows()),
        discharge=(10.0, _calm_flows()),  # 30p worse
    )
    assert await _select_optimised_key(
        _FakeHass(threshold=40), model, ["whole_horizon_cost", "discharge_cost"]
    ) == "discharge_cost"
    assert await _select_optimised_key(
        _FakeHass(), model, ["whole_horizon_cost", "discharge_cost"]
    ) == "whole_horizon_cost"


@pytest.mark.asyncio
async def test_zero_threshold_permits_only_non_regressive_swaps() -> None:
    """Threshold 0 means no regression tolerated - not the old unbounded behaviour."""
    regressive = _FakeModel(
        whole_horizon=(-20.0, _rapid_flows()),
        discharge=(-19.9, _calm_flows()),  # 0.1p worse
    )
    assert await _select_optimised_key(
        _FakeHass(threshold=0), regressive, ["whole_horizon_cost", "discharge_cost"]
    ) == "whole_horizon_cost"

    free = _FakeModel(
        whole_horizon=(-20.0, _rapid_flows()),
        discharge=(-20.0, _calm_flows()),  # equal cost
    )
    assert await _select_optimised_key(
        _FakeHass(threshold=0), free, ["whole_horizon_cost", "discharge_cost"]
    ) == "discharge_cost"


@pytest.mark.asyncio
async def test_non_finite_threshold_falls_back_to_default() -> None:
    model = _FakeModel(
        whole_horizon=(-20.0, _rapid_flows()),
        discharge=(50.0, _calm_flows()),  # 70p worse, far beyond any sane default
    )
    key = await _select_optimised_key(
        _FakeHass(threshold="unavailable"), model, ["whole_horizon_cost", "discharge_cost"]
    )
    assert key == "whole_horizon_cost"
    assert DEFAULTS[CONF_ALTERNATION_COST_THRESHOLD] == 10


@pytest.mark.asyncio
async def test_refusal_is_logged_with_costs_delta_and_threshold(caplog) -> None:
    model = _FakeModel(
        whole_horizon=(-23.6, _rapid_flows()),
        discharge=(52.7, _calm_flows()),
    )
    with caplog.at_level("WARNING"):
        await _select_optimised_key(_FakeHass(), model, ["whole_horizon_cost", "discharge_cost"])

    message = caplog.text
    assert "whole_horizon_cost" in message
    assert "discharge_cost" in message
    assert "-23.6" in message
    assert "52.7" in message
    assert "76.3" in message   # the delta
    assert "10.0" in message   # the threshold
    assert "charging" in message  # rapid-window summary
