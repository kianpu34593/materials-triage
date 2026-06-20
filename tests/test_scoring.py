"""Tests for the deterministic scoring helpers in materials_triage.core.scoring."""

import pytest

from materials_triage.core.scoring import normalize


def test_normalize_maximize_maps_highest_value_to_one():
    """For a 'maximize' target, the largest value is best and maps to 1, the
    smallest to 0, and the rest scale linearly between."""
    assert normalize([1.0, 2.0, 3.0], "maximize") == [0.0, 0.5, 1.0]


def test_normalize_minimize_maps_lowest_value_to_one():
    """For a 'minimize' target, the smallest value is best and maps to 1, the
    largest to 0 — the direction flips the scale."""
    assert normalize([1.0, 2.0, 3.0], "minimize") == [1.0, 0.5, 0.0]


def test_normalize_rejects_unknown_direction():
    """The Literal is only a hint at runtime, so normalize guards the direction
    itself — an unknown one is refused rather than silently treated as minimize."""
    with pytest.raises(ValueError, match="direction"):
        normalize([1.0, 2.0, 3.0], "max")


def test_normalize_degenerate_pool_maps_all_to_neutral_half():
    """When every value is the same the property carries no ranking signal, so
    each maps to a neutral 0.5 rather than dividing by a zero span."""
    assert normalize([2.0, 2.0, 2.0], "maximize") == [0.5, 0.5, 0.5]
    assert normalize([2.0, 2.0, 2.0], "minimize") == [0.5, 0.5, 0.5]


def test_normalize_rejects_empty_pool():
    """There is nothing to scale when no values are given, so normalize refuses
    an empty pool with a clear error rather than failing deep in min()."""
    with pytest.raises(ValueError, match="empty pool"):
        normalize([], "maximize")
