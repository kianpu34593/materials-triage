"""Spec-fidelity gate + deterministic facet seeders.

The LLM's hypothesis frequently *drops* hard requirements the goal states plainly
("oxide" -> require O, "simple composition" -> cap the element count, "non-toxic"
-> exclude toxic elements) or *invents* ones it didn't. This module closes the
"drops" gap deterministically, AFTER ``compile_spec``: it detects the facets a
goal asks for and, for any the compiled :class:`TriageSpec` fails to cover, seeds
them into the spec — so fidelity to the request is a code guarantee, not an LLM
hope. Each detect/seed decision is recorded as a :class:`FacetFinding` for the
audit trace and the human approval gate (where a seeded facet can still be edited
out).

Pure domain logic: no LLM, no I/O. Detection is conservative, literal phrase
matching (it does not understand negation — "not restricted to oxides" would be a
false positive; the human gate is the backstop). Seeding maps onto the current
spec vocabulary: required/excluded elements become ``ElementPredicate``s
(quantifier ``all`` / ``none``) and a count cap becomes a ``CountConstraint``.
"""

import re

from pydantic import BaseModel, ConfigDict

from materials_triage.core.schema import CountConstraint, ElementPredicate, TriageSpec

#: Substance-class noun -> the element it requires. "oxide" implies oxygen must be
#: present; "nitride" nitrogen; and so on. The defining chemistry of the request.
ANION_FAMILIES: dict[str, str] = {
    "oxide": "O",
    "nitride": "N",
    "carbide": "C",
    "sulfide": "S",
    "selenide": "Se",
    "telluride": "Te",
    "fluoride": "F",
    "chloride": "Cl",
    "bromide": "Br",
    "iodide": "I",
    "hydride": "H",
    "boride": "B",
    "phosphide": "P",
    "silicide": "Si",
    "arsenide": "As",
}

#: Elements toxic essentially regardless of oxidation state / clearly hazardous —
#: the basis of regulatory denylists (RoHS restricts Pb/Hg/Cd; REACH SVHC adds
#: As/Be/Sb/Tl) plus the radioactive elements. "non-toxic" seeds an exclusion of
#: this set. Composition is the workable toxicophore in materials science.
TOXIC_ELEMENTS: frozenset[str] = frozenset(
    {
        # RoHS / REACH heavy metals + metalloids
        "Pb",
        "Hg",
        "Cd",
        "As",
        "Be",
        "Tl",
        "Sb",
        # radioactive
        "Tc",
        "Pm",
        "Po",
        "At",
        "Rn",
        "Fr",
        "Ra",
        "Ac",
        "Th",
        "Pa",
        "U",
        "Np",
        "Pu",
        "Am",
        "Cm",
        "Bk",
        "Cf",
        "Es",
        "Fm",
        "Md",
        "No",
        "Lr",
    }
)

#: Toxicity here is oxidation-state / speciation / leachability dependent (Cr(VI)
#: vs Cr(III), soluble vs locked-in-lattice Ba) — distinctions a composition-only
#: rule cannot make and Materials Project cannot resolve. NOT auto-excluded;
#: surfaced as a caveat so the human knows the denylist is element-level only.
OXIDATION_STATE_DEPENDENT: frozenset[str] = frozenset(
    {"Cr", "Ni", "Co", "Ba", "V", "Se", "Mn", "Cu"}
)

#: Phrases asking to keep the chemistry simple -> the implied distinct-element cap.
_SIMPLICITY_CUES: tuple[tuple[str, int], ...] = (
    ("binary", 2),
    ("ternary", 3),
    ("quaternary", 4),
    ("simple composition", 3),
    ("simple chemistr", 3),
    ("simple stoichiometr", 3),
    ("compositionally simple", 3),
    ("few elements", 3),
)

#: Phrases that mean "exclude toxic elements".
_NONTOXIC_CUES: tuple[str, ...] = (
    "non-toxic",
    "nontoxic",
    "non toxic",
    "low-toxicity",
    "benign",
    "earth-abundant",
)


class FacetFinding(BaseModel):
    """One detect/seed decision the fidelity gate made, for the audit trace and the
    human gate. ``action`` is ``seeded`` (code added a facet the LLM omitted) or
    ``already_satisfied`` (the compiled spec already covered it)."""

    model_config = ConfigDict(frozen=True)

    facet: str
    cue: str
    action: str
    detail: str
    caveat: str = ""


def _found(cue: str, text: str) -> bool:
    """Stem match of ``cue`` in already-lowercased ``text``: a leading word boundary,
    no trailing one, so plurals and inflections match too ("oxide" matches
    "oxides"; "simple composition" matches "simple compositions")."""
    return re.search(rf"\b{re.escape(cue)}", text) is not None


def reconcile_spec(goal: str, spec: TriageSpec) -> tuple[TriageSpec, list[FacetFinding]]:
    """Detect the facets ``goal`` asks for and seed any the ``spec`` omits.

    Returns the (possibly augmented) spec and the findings. The result always
    satisfies :class:`TriageSpec`'s own coherence rules."""
    text = goal.lower()
    findings: list[FacetFinding] = []
    must_have = {e for p in spec.element_predicates if p.quantifier == "all" for e in p.members}
    must_lack = {e for p in spec.element_predicates if p.quantifier == "none" for e in p.members}
    seeded_required: set[str] = set()
    seeded_excluded: set[str] = set()

    # 1. Anion families: "oxide" -> require O, etc.
    for family, element in ANION_FAMILIES.items():
        if not _found(family, text):
            continue
        if element in must_have or element in seeded_required:
            findings.append(
                FacetFinding(
                    facet=family,
                    cue=family,
                    action="already_satisfied",
                    detail=f"require {element}",
                )
            )
        else:
            seeded_required.add(element)
            findings.append(
                FacetFinding(facet=family, cue=family, action="seeded", detail=f"require {element}")
            )

    # 2. Non-toxic: exclude the toxic set, minus anything explicitly required (an
    # explicit "arsenide" request wins over the blanket toxic exclusion of As).
    cue = next((c for c in _NONTOXIC_CUES if _found(c, text)), None)
    if cue is not None:
        wanted = TOXIC_ELEMENTS - must_have - seeded_required
        caveat = (
            "Element-level denylist only; toxicity of "
            f"{', '.join(sorted(OXIDATION_STATE_DEPENDENT))} is oxidation-state / "
            "leachability dependent and not resolvable from public DFT data — not auto-excluded."
        )
        if wanted <= must_lack:
            findings.append(
                FacetFinding(
                    facet="non-toxic",
                    cue=cue,
                    action="already_satisfied",
                    detail=f"exclude {len(wanted)} toxic elements",
                    caveat=caveat,
                )
            )
        else:
            seeded_excluded |= wanted - must_lack
            findings.append(
                FacetFinding(
                    facet="non-toxic",
                    cue=cue,
                    action="seeded",
                    detail=f"exclude {{{', '.join(sorted(wanted))}}}",
                    caveat=caveat,
                )
            )

    # 3. Simple composition: cap the distinct-element count, never below the count
    # of elements that must be present (else the spec contradicts itself).
    seeded_count: CountConstraint | None = None
    implied = [n for c, n in _SIMPLICITY_CUES if _found(c, text)]
    if implied:
        scue = next(c for c, n in _SIMPLICITY_CUES if _found(c, text))
        target = max(min(implied), len(must_have | seeded_required))
        existing_max = spec.count.max if spec.count is not None else None
        if existing_max is not None and existing_max <= target:
            findings.append(
                FacetFinding(
                    facet="simple composition",
                    cue=scue,
                    action="already_satisfied",
                    detail=f"count max={existing_max}",
                )
            )
        else:
            seeded_count = CountConstraint(max=target)
            findings.append(
                FacetFinding(
                    facet="simple composition",
                    cue=scue,
                    action="seeded",
                    detail=f"count max={target}",
                )
            )

    if not any(f.action == "seeded" for f in findings):
        return spec, findings

    predicates = spec.element_predicates
    if seeded_required:
        predicates = predicates + (
            ElementPredicate(quantifier="all", members=frozenset(seeded_required)),
        )
    if seeded_excluded:
        predicates = predicates + (
            ElementPredicate(quantifier="none", members=frozenset(seeded_excluded)),
        )
    update: dict = {"element_predicates": predicates}
    if seeded_count is not None:
        update["count"] = seeded_count
    new_spec = spec.model_copy(update=update)
    return new_spec, findings
