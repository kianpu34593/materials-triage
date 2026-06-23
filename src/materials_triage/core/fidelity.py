"""Spec-fidelity gate + deterministic facet seeders.

The LLM's hypothesis frequently *drops* hard requirements the goal states plainly
("oxide" -> require O, "simple composition" -> max_nelements, "non-toxic" ->
exclude toxic elements) or *invents* ones it didn't. This module closes that gap
deterministically, AFTER ``compile_spec``: it detects the facets a goal asks for
and, for any the compiled :class:`TriageSpec` fails to cover, seeds them into the
spec — so fidelity to the request is a code guarantee, not an LLM hope. Each
detect/seed decision is recorded as a :class:`FacetFinding` for the audit trace
and the human approval gate (where a seeded facet can still be edited out).

Pure domain logic: no LLM, no I/O. Detection is conservative, literal phrase
matching (it does not understand negation — "not restricted to oxides" would be a
false positive; the human gate is the backstop).
"""

import re

from pydantic import BaseModel, ConfigDict

from materials_triage.core.schema import TriageSpec

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
#: this set. See the materials-toxicity discussion: composition is the workable
#: toxicophore in materials science.
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

#: Composition-simplicity phrases -> the max distinct-element count they imply.
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


class FacetFinding(BaseModel):
    """One detect/seed decision the fidelity gate made, for the audit trace and
    the human gate. ``action`` is ``seeded`` (code added it — the LLM omitted it)
    or ``already_satisfied`` (the LLM's spec already covered it)."""

    model_config = ConfigDict(frozen=True)

    facet: str
    cue: str
    action: str
    detail: str
    caveat: str = ""


def _found(cue: str, text: str) -> bool:
    """Stem match of ``cue`` in already-lowercased ``text``: a leading word
    boundary, no trailing one, so plurals and inflections match too ("oxide"
    matches "oxides"; "simple composition" matches "simple compositions")."""
    return re.search(rf"\b{re.escape(cue)}", text) is not None


def reconcile_spec(goal: str, spec: TriageSpec) -> tuple[TriageSpec, list[FacetFinding]]:
    """Detect the facets ``goal`` asks for and seed any the ``spec`` omits.

    Returns the (possibly augmented) spec and the findings. Required elements seeded
    from an anion family are never also excluded as toxic (the explicit request
    wins), and ``max_nelements`` is never set below the required-element count, so
    the result always satisfies :class:`TriageSpec`'s own coherence rules."""
    text = goal.lower()
    findings: list[FacetFinding] = []
    required = set(spec.required_elements)
    excluded = set(spec.excluded_elements)
    max_nelements = spec.max_nelements

    # 1. Anion families: "oxide" -> require O, etc.
    for family, element in ANION_FAMILIES.items():
        if not _found(family, text):
            continue
        if element in required:
            findings.append(
                FacetFinding(
                    facet=family,
                    cue=family,
                    action="already_satisfied",
                    detail=f"require {element}",
                )
            )
        else:
            required.add(element)
            findings.append(
                FacetFinding(facet=family, cue=family, action="seeded", detail=f"require {element}")
            )

    # 2. Non-toxic: exclude the toxic set (minus anything explicitly required).
    nontoxic_cue = next(
        (
            c
            for c in (
                "non-toxic",
                "nontoxic",
                "non toxic",
                "low-toxicity",
                "benign",
                "earth-abundant",
            )
            if _found(c, text)
        ),
        None,
    )
    if nontoxic_cue is not None:
        wanted = TOXIC_ELEMENTS - required
        caveat = (
            "Element-level denylist only; toxicity of "
            f"{', '.join(sorted(OXIDATION_STATE_DEPENDENT))} is oxidation-state / "
            "leachability dependent and not resolvable from public DFT data — not auto-excluded."
        )
        if wanted <= excluded:
            findings.append(
                FacetFinding(
                    facet="non-toxic",
                    cue=nontoxic_cue,
                    action="already_satisfied",
                    detail=f"exclude {len(wanted)} toxic elements",
                    caveat=caveat,
                )
            )
        else:
            excluded |= wanted
            findings.append(
                FacetFinding(
                    facet="non-toxic",
                    cue=nontoxic_cue,
                    action="seeded",
                    detail=f"exclude {{{', '.join(sorted(wanted))}}}",
                    caveat=caveat,
                )
            )

    # 3. Simple composition: cap the distinct-element count.
    implied = [n for cue, n in _SIMPLICITY_CUES if _found(cue, text)]
    if implied:
        cue = next(c for c, n in _SIMPLICITY_CUES if _found(c, text))
        target = max(min(implied), len(required))  # never below the required count
        if max_nelements is not None and max_nelements <= target:
            findings.append(
                FacetFinding(
                    facet="simple composition",
                    cue=cue,
                    action="already_satisfied",
                    detail=f"max_nelements={max_nelements}",
                )
            )
        else:
            max_nelements = target
            findings.append(
                FacetFinding(
                    facet="simple composition",
                    cue=cue,
                    action="seeded",
                    detail=f"max_nelements={target}",
                )
            )

    seeded = any(f.action == "seeded" for f in findings)
    if not seeded:
        return spec, findings
    new_spec = spec.model_copy(
        update={
            "required_elements": frozenset(required),
            "excluded_elements": frozenset(excluded),
            "max_nelements": max_nelements,
        }
    )
    return new_spec, findings
