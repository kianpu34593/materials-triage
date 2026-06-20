"""Tests for the weighted-average ranker in materials_triage.core.ranking."""

from materials_triage.core.ranking import rank
from materials_triage.core.schema import Candidate, PropertyValue, Provenance, RankingTarget


def _candidate(identifier: str, formula: str, **props: float) -> Candidate:
    """Build a candidate whose named properties carry a value in eV and a receipt."""
    return Candidate(
        identifier=identifier,
        formula=formula,
        properties={
            name: PropertyValue(
                value=value,
                unit="eV",
                provenance=Provenance(source="Materials Project", record_id=identifier),
            )
            for name, value in props.items()
        },
    )


def test_rank_orders_survivors_best_first_by_single_target():
    """One maximize target: the higher value normalizes to 1, the lower to 0, and
    the result lists the better candidate first with that weighted score."""
    low = _candidate("mp-low", "X", band_gap=1.0)
    high = _candidate("mp-high", "Y", band_gap=3.0)
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = rank([low, high], (target,))

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-high", "mp-low"]
    assert [sc.score for sc in result.ranked] == [1.0, 0.0]
    assert result.excluded == ()


def test_rank_combines_two_targets_by_weight_and_records_contributions():
    """Two conflicting targets: a heavier band_gap weight tips the order, and each
    candidate's contributions map the weighted share per target, summing to score."""
    big_gap = _candidate("mp-gap", "X", band_gap=3.0, density=10.0)  # best gap, worst density
    dense = _candidate("mp-dense", "Y", band_gap=1.0, density=5.0)  # worst gap, best density
    gap = RankingTarget(property_name="band_gap", direction="maximize", weight=0.6)
    density = RankingTarget(property_name="density", direction="minimize", weight=0.4)

    result = rank([big_gap, dense], (gap, density))

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-gap", "mp-dense"]
    top, second = result.ranked
    assert top.contributions == {"band_gap": 0.6, "density": 0.0}
    assert second.contributions == {"band_gap": 0.0, "density": 0.4}
    assert top.score == sum(top.contributions.values())
    assert second.score == sum(second.contributions.values())


def test_rank_keeps_imputed_candidate_and_flags_the_missing_property():
    """A candidate missing an impute_medium target still ranks (imputed 0.5) and
    is not excluded, but its flagged_missing records the imputed property so the
    audit never mistakes the gap for a measured value."""
    low = _candidate("mp-low", "X", band_gap=1.0)
    high = _candidate("mp-high", "Y", band_gap=3.0)
    miss = _candidate("mp-miss", "Z")  # no band_gap -> imputed 0.5
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = rank([low, high, miss], (target,))

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-high", "mp-miss", "mp-low"]
    assert result.excluded == ()
    by_id = {sc.candidate.identifier: sc for sc in result.ranked}
    assert by_id["mp-miss"].flagged_missing == frozenset({"band_gap"})
    assert by_id["mp-high"].flagged_missing == frozenset()


def test_rank_excludes_candidate_missing_an_exclude_policy_target():
    """A candidate missing a target whose policy is 'exclude' never reaches the
    ranking: it is absent from ranked and recorded in excluded as missing_data."""
    has_it = _candidate("mp-has", "X", band_gap=2.0)
    miss = _candidate("mp-miss", "Z")  # no band_gap
    target = RankingTarget(
        property_name="band_gap", direction="maximize", weight=1.0, on_missing="exclude"
    )

    result = rank([has_it, miss], (target,))

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-has"]
    assert len(result.excluded) == 1
    assert result.excluded[0].candidate.identifier == "mp-miss"
    assert result.excluded[0].reason == "missing_data"


def test_rank_breaks_ties_by_retrieval_order():
    """Candidates that earn the same score keep their incoming (retrieval) order,
    so ranking is a stable, reproducible sort rather than an arbitrary shuffle."""
    tie1 = _candidate("mp-tie1", "X", band_gap=3.0)
    tie2 = _candidate("mp-tie2", "Y", band_gap=3.0)
    lower = _candidate("mp-low", "Z", band_gap=1.0)
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = rank([tie1, tie2, lower], (target,))

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-tie1", "mp-tie2", "mp-low"]
    assert result.ranked[0].score == result.ranked[1].score


def test_rank_with_no_targets_scores_all_zero_in_retrieval_order():
    """A constraints-only spec reaches the ranker with no targets: there is no
    ordering signal, so every survivor scores 0.0 and keeps its retrieval order —
    the shortlist is still the valid filtered set, just unranked."""
    first = _candidate("mp-first", "X", band_gap=2.0)
    second = _candidate("mp-second", "Y", band_gap=1.0)

    result = rank([first, second], ())

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-first", "mp-second"]
    assert [sc.score for sc in result.ranked] == [0.0, 0.0]
    assert all(sc.contributions == {} for sc in result.ranked)
    assert result.excluded == ()
