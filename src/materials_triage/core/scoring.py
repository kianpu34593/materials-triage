"""Deterministic scoring helpers: pure functions over property values.

These compute the numbers the ranker combines; they hold no state and touch no
data model. Normalisation maps a property's values onto a comparable [0, 1]
scale so the weighted average can sum unlike units.
"""

from typing import Literal


def normalize(values: list[float], direction: Literal["maximize", "minimize"]) -> list[float]:
    """Min-max normalise ``values`` onto [0, 1], where 1 is best for ``direction``."""
    if direction not in ("maximize", "minimize"):
        raise ValueError(f"direction must be 'maximize' or 'minimize', got {direction!r}")
    if not values:
        raise ValueError("cannot normalize an empty pool of values")
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        # Every value is identical: the property cannot discriminate, so it
        # contributes a neutral 0.5 rather than dividing by a zero span.
        return [0.5 for _ in values]
    if direction == "maximize":
        return [(v - lo) / span for v in values]
    return [(hi - v) / span for v in values]
