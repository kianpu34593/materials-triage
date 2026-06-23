"""Core pydantic data models for the triage pipeline.

Every scientific value flowing through the pipeline carries a ``Provenance`` so
that the output validator can confirm nothing was invented by the LLM.
"""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from materials_triage.core.elements import ELEMENT_SYMBOLS


class Provenance(BaseModel):
    """Records where a scientific value came from, and how trustworthy it is.

    Beyond the bare source/record id, it carries trust metadata: ``method``
    (how the value was produced — experimental, computational, ml_predicted, or
    literature) and, for DFT-computed values, the ``xc_functional`` of the
    producing calculation (``None`` when untraceable or not applicable).

    Immutable: once a value is tagged with its origin, that tag cannot change
    as it travels downstream.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    method: Literal["experimental", "computational", "ml_predicted", "literature"]
    xc_functional: str | None = None


class PropertyValue(BaseModel):
    """A scientific value bound to the receipt proving where it came from.

    The number and its unit are one inseparable fact (``unit`` is ``None`` for a
    genuinely dimensionless quantity — refractive index, dielectric constant); every
    value carries a ``Provenance`` so the output validator can confirm nothing was
    invented. This is a deterministic-layer model the adapter fills from trusted API
    data, not an LLM-built one, so its constraints are internal invariants.
    """

    model_config = ConfigDict(frozen=True)

    value: float | None = None
    unit: str | None
    missing: bool = False
    provenance: Provenance

    @model_validator(mode="after")
    def _presence_matches_value(self) -> Self:
        if self.missing and self.value is not None:
            raise ValueError("a missing value cannot carry a number")
        if not self.missing and self.value is None:
            raise ValueError("a present value must carry a number")
        return self


class Candidate(BaseModel):
    """A material returned by retrieval: its source-issued identity plus the
    canonical, provenance-tagged properties the filter and ranker read by name.
    """

    model_config = ConfigDict(frozen=True)

    identifier: str = Field(min_length=1)
    formula: str = Field(min_length=1)
    properties: Mapping[str, PropertyValue] = Field(default_factory=dict)
    #: The distinct element symbols in the composition — populated when retrieval asks
    #: for it, so the deterministic filter can enforce element predicates a source
    #: can't push (e.g. an ``any`` membership). Empty when composition wasn't fetched.
    elements: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _freeze_properties(self) -> Self:
        if not isinstance(self.properties, MappingProxyType):
            frozen = MappingProxyType(dict(self.properties))
            object.__setattr__(self, "properties", frozen)
        return self

    @field_serializer("properties")
    def _serialize_properties(self, value: Mapping[str, PropertyValue]) -> dict[str, PropertyValue]:
        return dict(value)


class Constraint(BaseModel):
    """A hard filter on one property: candidates outside the bound are dropped.

    A bound is expressed as an inclusive ``min`` and/or ``max``; the hard-filter
    stage reads these to gate candidates.
    """

    model_config = ConfigDict(frozen=True)

    property_name: str = Field(min_length=1)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds_some_property(self) -> Self:
        if self.min is None and self.max is None:
            raise ValueError("a constraint must set at least one of min or max")
        for name, bound in (("min", self.min), ("max", self.max)):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"a constraint's {name} must be a finite number")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("a constraint's min cannot exceed its max")
        return self


class BooleanConstraint(BaseModel):
    """A hard filter on one boolean property: a candidate whose value does not
    match ``required`` is dropped.

    The source-neutral analog of :class:`Constraint` for the yes/no facts a source
    exposes (e.g. ``is_stable``, ``is_metal``) — things a numeric min/max cannot
    express. Like :class:`Constraint` it does not restrict ``property_name``: which
    booleans a source can actually answer is the adapter's vocabulary concern.
    """

    model_config = ConfigDict(frozen=True)

    property_name: str = Field(min_length=1)
    required: bool


class ElementPredicate(BaseModel):
    """A hard composition filter: a quantified membership test over element symbols.

    Unifies require/exclude into one source-neutral predicate matching the OPTIMADE
    element operators — ``all`` (every member present), ``any`` (at least one
    present, the operator a require/exclude pair could not express), ``none`` (no
    member present). Members are validated against the canonical element set so a
    hallucinated symbol is refused before it reaches the spec.
    """

    model_config = ConfigDict(frozen=True)

    quantifier: Literal["all", "any", "none"]
    members: frozenset[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_are_real(self) -> Self:
        unknown = self.members - ELEMENT_SYMBOLS
        if unknown:
            raise ValueError(f"composition rule names non-elements: {sorted(unknown)}")
        return self


class CountConstraint(BaseModel):
    """A hard filter on the number of distinct elements in a composition.

    A bound on composition cardinality ("simple compositions" → few elements),
    expressed as an inclusive ``min`` and/or ``max``. Modelled as its own typed
    field rather than a numeric :class:`Constraint` on a magic property name,
    because it takes part in a cross-field coherence check with the element
    predicates (you cannot require more distinct elements than the cap allows) — a
    typed slot keeps that invariant robust instead of string-matching a name.
    """

    model_config = ConfigDict(frozen=True)

    min: int | None = Field(default=None, ge=1)
    max: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _bounds_some_count(self) -> Self:
        if self.min is None and self.max is None:
            raise ValueError("a count constraint must set at least one of min or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("a count constraint's min cannot exceed its max")
        return self


class RankingTarget(BaseModel):
    """A soft scoring preference on one property: the ranker maps the property to
    a desirability in [0, 1] in the given direction and weights it in the
    weighted geometric mean.

    ``direction`` is the shape of that desirability curve: ``maximize`` /
    ``minimize`` are monotonic (bigger / smaller is always better), while
    ``target`` is the non-monotonic "moderate is best" case — desirability peaks
    at ``target`` and falls to zero away from it. The window anchors
    (``lower`` / ``target`` / ``upper``) are absolute. A ramp's two anchors share
    a source: ``maximize`` (``lower``/``target``) and ``minimize``
    (``target``/``upper``) take both from the spec or fill both from the candidate
    pool's extremes, so they must be announced together or not at all; a
    ``target`` direction names its full window explicitly (no pool fallback).
    ``curvature`` (an exponent ``> 0``) bends the curve between the anchors:
    ``1`` linear, ``> 1`` strict (credit only near the ideal), ``< 1`` lenient.

    ``weight`` is a proportional share in ``(0, 1]``; across the targets of a
    single ``TriageSpec`` the weights must sum to 1.
    """

    model_config = ConfigDict(frozen=True)

    property_name: str = Field(min_length=1)
    direction: Literal["maximize", "minimize", "target"]
    weight: float = Field(gt=0, le=1)
    on_missing: Literal["exclude", "impute_medium"] = "impute_medium"
    lower: float | None = None
    target: float | None = None
    upper: float | None = None
    curvature: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _anchors_share_a_source(self) -> Self:
        # A desirability ramp's two anchors must come from the same source —
        # both announced in the spec, or both filled from the candidate pool —
        # so the curve's span can never go negative (see ``resolve_bounds``).
        # ``maximize`` ramps lower->target, ``minimize`` ramps target->upper, and
        # ``target`` names its full lower->target->upper window (no pool fallback).
        if self.direction == "maximize":
            self._reject_unused_anchor("upper", self.upper)
            self._require_both_or_neither(("lower", self.lower), ("target", self.target))
        elif self.direction == "minimize":
            self._reject_unused_anchor("lower", self.lower)
            self._require_both_or_neither(("target", self.target), ("upper", self.upper))
        else:
            self._require_full_window()
        return self

    def _reject_unused_anchor(self, name: str, value: float | None) -> None:
        if value is not None:
            raise ValueError(
                f"a '{self.direction}' direction does not use {name}, but {name}={value} is set"
            )

    def _require_both_or_neither(
        self, low: tuple[str, float | None], high: tuple[str, float | None]
    ) -> None:
        (lo_name, lo), (hi_name, hi) = low, high
        if (lo is None) != (hi is None):
            present, missing = (lo_name, hi_name) if lo is not None else (hi_name, lo_name)
            raise ValueError(
                f"a '{self.direction}' direction needs {lo_name} and {hi_name} announced "
                f"together or not at all, but {present} is set without {missing}"
            )
        if lo is not None and lo >= hi:
            raise ValueError(
                f"window anchors must strictly ascend, but {lo_name}={lo} "
                f"does not precede {hi_name}={hi}"
            )

    def _require_full_window(self) -> None:
        for name, value in (("lower", self.lower), ("target", self.target), ("upper", self.upper)):
            if value is None:
                raise ValueError(
                    f"a 'target' direction must announce its full window, but {name} is unset"
                )
        if not self.lower < self.target < self.upper:
            raise ValueError(
                "window anchors must strictly ascend, but "
                f"lower={self.lower}, target={self.target}, upper={self.upper} do not"
            )


class TriageSpec(BaseModel):
    """The fully-resolved request the deterministic pipeline consumes:
    the hard filters it gates on, the soft targets it ranks by, the
    composition rules that scope retrieval, and the ranking method that
    combines the soft targets (``arithmetic_mean`` — the compensatory weighted
    average, the default — or ``geometric_mean`` — the non-compensatory
    geometric mean of desirability curves). The method is on the spec so a run
    records and replays it.
    """

    model_config = ConfigDict(frozen=True)

    constraints: tuple[Constraint, ...] = ()
    boolean_constraints: tuple[BooleanConstraint, ...] = ()
    ranking_targets: tuple[RankingTarget, ...] = ()
    element_predicates: tuple[ElementPredicate, ...] = ()
    count: CountConstraint | None = None
    ranking_method: Literal["arithmetic_mean", "geometric_mean"] = "arithmetic_mean"

    @model_validator(mode="after")
    def _has_a_hard_filter(self) -> Self:
        if not (
            self.constraints
            or self.boolean_constraints
            or self.element_predicates
            or self.count is not None
        ):
            raise ValueError("a spec must have at least one hard filter")
        seen: set[str] = set()
        for c in self.constraints:
            if c.property_name in seen:
                raise ValueError(
                    f"property {c.property_name!r} is constrained more than once; "
                    "combine bounds into a single constraint"
                )
            seen.add(c.property_name)
        seen_bool: set[str] = set()
        for b in self.boolean_constraints:
            if b.property_name in seen_bool:
                raise ValueError(
                    f"boolean property {b.property_name!r} is constrained more than once; "
                    "a boolean property should have a single required value"
                )
            seen_bool.add(b.property_name)
        ranked: set[str] = set()
        for t in self.ranking_targets:
            if t.property_name in ranked:
                raise ValueError(
                    f"property {t.property_name!r} is ranked more than once; "
                    "a property should have a single ranking target"
                )
            ranked.add(t.property_name)
        if self.ranking_targets:
            total = math.fsum(t.weight for t in self.ranking_targets)
            if not math.isclose(total, 1.0, abs_tol=1e-9):
                raise ValueError(
                    f"ranking weights are proportional shares and must sum to 1, "
                    f"but they sum to {total}"
                )
        # ElementPredicate validates its own members, so the spec only checks
        # cross-predicate coherence: elements that must ALL be present (the union of
        # every "all"-quantified predicate) cannot also be forbidden by a "none"
        # predicate, nor outnumber the count cap. ("any" members need not all be
        # present, so they bind neither check.)
        must_have = frozenset(
            e for p in self.element_predicates if p.quantifier == "all" for e in p.members
        )
        must_lack = frozenset(
            e for p in self.element_predicates if p.quantifier == "none" for e in p.members
        )
        contradictory = must_have & must_lack
        if contradictory:
            raise ValueError(
                f"elements cannot be both required and excluded: {sorted(contradictory)}"
            )
        for p in self.element_predicates:
            if p.quantifier == "any" and p.members <= must_lack:
                raise ValueError(
                    "an 'any' predicate cannot be satisfied when all its members are "
                    f"excluded: {sorted(p.members)}"
                )
        cap = self.count.max if self.count is not None else None
        if cap is not None and len(must_have) > cap:
            raise ValueError(
                f"required elements demand {len(must_have)} distinct "
                f"elements but the count constraint caps it at {cap}"
            )
        # Desirability anchors and curvature are a geometric_mean-only feature, so
        # neither ranker can silently ignore or mis-handle them. The geometric_mean
        # ranker zeros a candidate at an acceptability floor, so it requires every
        # target to announce its ramp bounds (no pool fallback) — then a desirability
        # of 0 means a genuine floor failure, not merely pool-worst. The
        # arithmetic_mean ranker normalises via normalize() (pool extremes only), so
        # it cannot honour anchors or curvature; reject them rather than drop them.
        if self.ranking_method == "geometric_mean":
            for t in self.ranking_targets:
                required = {
                    "maximize": (("lower", t.lower), ("target", t.target)),
                    "minimize": (("target", t.target), ("upper", t.upper)),
                    "target": (("lower", t.lower), ("target", t.target), ("upper", t.upper)),
                }[t.direction]
                missing = [name for name, value in required if value is None]
                if missing:
                    raise ValueError(
                        f"ranking_method='geometric_mean' requires {t.property_name!r} to "
                        f"announce its ramp bounds, but {missing} are unset"
                    )
        else:
            # A 'target' direction is itself a desirability window — name the method
            # to fix rather than the anchors, since dropping anchors won't help it.
            targeted = [t.property_name for t in self.ranking_targets if t.direction == "target"]
            if targeted:
                raise ValueError(
                    f"a 'target' direction (here on {targeted}) is a desirability window "
                    "only the geometric_mean ranker scores, but ranking_method is "
                    f"{self.ranking_method!r}; set ranking_method='geometric_mean'"
                )
            configured = [
                t.property_name
                for t in self.ranking_targets
                if t.lower is not None
                or t.target is not None
                or t.upper is not None
                or t.curvature != 1.0
            ]
            if configured:
                raise ValueError(
                    f"anchors and curvature configure desirability curves (here on "
                    f"{configured}) and are only valid with ranking_method='geometric_mean', "
                    f"but ranking_method is {self.ranking_method!r}"
                )
        return self


class PredicateRouting(BaseModel):
    """How a source routes a spec's hard predicates between server-side push and
    local enforcement.

    The ``local`` buckets are the *exclusive set* — predicates the source can return
    data for but cannot filter server-side (retrievable but not queryable, e.g. MP's
    ``is_magnetic`` or an element ``any``) — which the deterministic filter must
    enforce. ``caveats`` name predicates the source can neither push nor return data
    for, so they go unenforced and must be surfaced loudly. Predicates the source
    pushes server-side appear in no bucket (already handled by retrieval).
    """

    model_config = ConfigDict(frozen=True)

    local_booleans: tuple[BooleanConstraint, ...] = ()
    local_element_predicates: tuple[ElementPredicate, ...] = ()
    caveats: tuple[str, ...] = ()


class RetrievalResult(BaseModel):
    """What a source's ``retrieve`` returns: the candidates it fetched plus any
    run-level ``caveats`` the I/O loop must surface loudly. The caveats channel is
    how retrieval reports that it could not return the *complete* filtered set — e.g.
    the result was capped at a page ceiling, so ranking sees only a subset — without
    silently truncating. Predicate-routing caveats (``PredicateRouting.caveats``) are
    a separate stage; these are retrieval's own.
    """

    model_config = ConfigDict(frozen=True)

    candidates: tuple[Candidate, ...] = ()
    caveats: tuple[str, ...] = ()


class ScoredCandidate(BaseModel):
    """A survivor of the hard filters, paired with the composite score it earned
    and the per-target contributions that produced it, so the audit view can
    show how the ranking was reached.
    """

    model_config = ConfigDict(frozen=True)

    candidate: Candidate
    score: float
    contributions: Mapping[str, float] = Field(default_factory=dict)
    flagged_missing: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _freeze_contributions(self) -> Self:
        if not math.isfinite(self.score):
            raise ValueError("a score must be a finite number")
        if not isinstance(self.contributions, MappingProxyType):
            frozen = MappingProxyType(dict(self.contributions))
            object.__setattr__(self, "contributions", frozen)
        return self

    @field_serializer("contributions")
    def _serialize_contributions(self, value: Mapping[str, float]) -> dict[str, float]:
        return dict(value)


class ExcludedCandidate(BaseModel):
    """A candidate dropped by the hard filters, paired with the structured
    reason it was dropped — the property, a machine-readable reason, and the
    offending value against the bound it violated — so the audit can explain
    the exclusion without re-reading the spec.
    """

    model_config = ConfigDict(frozen=True)

    candidate: Candidate
    property_name: str = Field(min_length=1)
    reason: Literal[
        "below_min", "above_max", "missing_data", "boolean_mismatch", "element_mismatch"
    ]
    value: float | None = None
    bound: float | None = None

    @model_validator(mode="after")
    def _reason_agrees_with_evidence(self) -> Self:
        if self.reason in ("missing_data", "boolean_mismatch", "element_mismatch"):
            # No numeric bound to record: the property was absent (missing_data), a
            # boolean that didn't match the required flag (boolean_mismatch), or a
            # composition that failed an element predicate (element_mismatch); a value
            # here would contradict the reason.
            if self.value is not None:
                raise ValueError(f"a {self.reason!r} exclusion cannot carry a value")
            return self
        if self.value is None or self.bound is None:
            raise ValueError(
                f"a {self.reason!r} exclusion must record both the value and the bound"
            )
        if self.reason == "below_min" and not self.value < self.bound:
            raise ValueError(
                f"'below_min' requires value < bound, got {self.value} >= {self.bound}"
            )
        if self.reason == "above_max" and not self.value > self.bound:
            raise ValueError(
                f"'above_max' requires value > bound, got {self.value} <= {self.bound}"
            )
        return self


class TriageResult(BaseModel):
    """The outcome both renderers read: the ranked survivors of the hard filters
    alongside the candidates that were dropped, each with its reason.
    """

    model_config = ConfigDict(frozen=True)

    ranked: tuple[ScoredCandidate, ...] = ()
    excluded: tuple[ExcludedCandidate, ...] = ()

    @model_validator(mode="after")
    def _ranked_is_best_first(self) -> Self:
        scores = [sc.score for sc in self.ranked]
        if any(earlier < later for earlier, later in zip(scores, scores[1:], strict=False)):
            raise ValueError("ranked survivors must be stored in non-increasing score order")
        return self
