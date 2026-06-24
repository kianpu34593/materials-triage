"""Dev-only generator for the Materials Project property vocabulary.

Parses the MP ``/openapi.json`` schema *at build time* and emits a committed
field-table (``src/materials_triage/sources/_mp_fields.py``) the adapter reads.
The schema is vendored (``tools/mp_summary_schema.json``), never fetched at
runtime, so every triage run stays replayable (see ADR/handoff).

Regenerate with::

    python tools/gen_mp_vocab.py

The pure parsing functions here are exercised offline against a fixture sliced
from the live schema; ``generate_table`` is checked against the full vendored one.
"""

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VENDORED_SCHEMA = _HERE / "mp_summary_schema.json"
_OUTPUT = _HERE.parent / "src" / "materials_triage" / "sources" / "_mp_fields.py"


def _scalar_type(prop: dict) -> str | None:
    """Resolve a JSON-Schema property to its scalar ``type``.

    MP wraps every optional field as ``anyOf: [{type: X}, {type: null}]``; a
    required field is a plain ``{type: X}``. Return the non-null branch's type
    (e.g. ``"number"``, ``"boolean"``, ``"integer"``), or ``None`` when no
    concrete scalar type is present (e.g. the untyped ``material_id``)."""
    if "type" in prop:
        return prop["type"]
    for branch in prop.get("anyOf", []):
        branch_type = branch.get("type")
        if branch_type and branch_type != "null":
            return branch_type
    return None


#: Scalar type categories that belong in the vocabulary — the fields a hypothesis
#: can sensibly filter or rank on. Free-text ``string`` and ``array`` fields (and
#: untyped identity fields the parser already drops) are excluded.
_VOCABULARY_TYPES = frozenset({"number", "integer", "boolean"})

#: Boolean fields that describe the *document*, not the material — the LLM must not
#: filter a shortlist on them, so they're denied by name even though they pass the
#: type filter.
_EXCLUDED_FIELDS = frozenset({"deprecated", "theoretical", "has_reconstructed"})

#: Object-typed fields the adapter collapses to a single scalar, so they belong in
#: the vocabulary despite their ``object`` type. MP returns the elastic moduli as a
#: Voigt-Reuss-Hill dict ``{voigt, reuss, vrh}``; the adapter's ``_scalar`` takes the
#: VRH average. The schema can't distinguish these from ``composition`` /
#: ``composition_reduced`` (also object-of-number, but element->amount maps), so they
#: must be named explicitly — mirroring the exact fields ``_scalar`` already handles.
_VRH_OBJECT_FIELDS = frozenset({"bulk_modulus", "shear_modulus"})

#: Hand-pinned ``{field: {unit, origin}}`` for every vocabulary field. Neither value
#: is in the schema: ``unit`` is pinned tribal knowledge (``None`` = dimensionless or
#: a bare count); ``origin`` is the MP ``origins[]`` property-doc name whose task
#: carries the value's XC functional, confirmed against live data. ``origin=None`` is
#: an *explicit* "no traceable functional" decision — elasticity has no ``origins[]``
#: entry at all, surface energies trace only to method-named docs, and counts /
#: composition are functional-independent. The build_table lockstep guard fails if a
#: vocabulary field is missing here, so this stays in step with the schema surface.
_FIELD_META: dict[str, dict] = {
    # electronic_structure
    "band_gap": {"unit": "eV", "origin": "electronic_structure"},
    "cbm": {"unit": "eV", "origin": "electronic_structure"},
    "vbm": {"unit": "eV", "origin": "electronic_structure"},
    "efermi": {"unit": "eV", "origin": "electronic_structure"},
    "dos_energy_up": {"unit": "eV", "origin": "electronic_structure"},
    "dos_energy_down": {"unit": "eV", "origin": "electronic_structure"},
    "is_metal": {"unit": None, "origin": "electronic_structure"},
    "is_gap_direct": {"unit": None, "origin": "electronic_structure"},
    # energy / stability
    "formation_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "energy_above_hull": {"unit": "eV/atom", "origin": "energy"},
    "energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "uncorrected_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "equilibrium_reaction_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "is_stable": {"unit": None, "origin": "energy"},
    # structure
    "density": {"unit": "g/cm³", "origin": "structure"},
    "density_atomic": {"unit": "Å³/atom", "origin": "structure"},
    "volume": {"unit": "Å³", "origin": "structure"},
    "nsites": {"unit": None, "origin": "structure"},
    # magnetism
    "total_magnetization": {"unit": "μB", "origin": "magnetism"},
    "total_magnetization_normalized_formula_units": {"unit": "μB/f.u.", "origin": "magnetism"},
    "total_magnetization_normalized_vol": {"unit": "μB/Å³", "origin": "magnetism"},
    "is_magnetic": {"unit": None, "origin": "magnetism"},
    "num_magnetic_sites": {"unit": None, "origin": "magnetism"},
    "num_unique_magnetic_sites": {"unit": None, "origin": "magnetism"},
    # dielectric (dimensionless)
    "n": {"unit": None, "origin": "dielectric"},
    "e_total": {"unit": None, "origin": "dielectric"},
    "e_electronic": {"unit": None, "origin": "dielectric"},
    "e_ionic": {"unit": None, "origin": "dielectric"},
    # piezoelectric
    "e_ij_max": {"unit": "C/m²", "origin": "piezoelectric"},
    # elasticity — values present but no origins[] entry, so functional is untraceable
    "bulk_modulus": {"unit": "GPa", "origin": None},
    "shear_modulus": {"unit": "GPa", "origin": None},
    "homogeneous_poisson": {"unit": None, "origin": None},
    "universal_anisotropy": {"unit": None, "origin": None},
    # surface — trace only to method-named docs (hinuma/latimer_munro/…), ambiguous
    "weighted_surface_energy": {"unit": "J/m²", "origin": None},
    "weighted_surface_energy_EV_PER_ANG2": {"unit": "eV/Å²", "origin": None},
    "weighted_work_function": {"unit": "eV", "origin": None},
    "surface_anisotropy": {"unit": None, "origin": None},
    "shape_factor": {"unit": None, "origin": None},
    # composition — functional-independent
    "nelements": {"unit": None, "origin": None},
}


def vocabulary_fields(fields: dict[str, str]) -> dict[str, str]:
    """Keep only the vocabulary-eligible fields from a parsed ``{name: type}`` map.

    The vocabulary the spec-building prompt hands the LLM is the numeric/boolean
    surface (plus the VRH-collapsible moduli); strings, arrays, composition maps,
    and untyped identity fields can't anchor a numeric constraint or a ranking
    target, so they're dropped here. (This selects *which property names exist* — it
    does not build any API query; that is #38.)"""
    return {
        name: t
        for name, t in fields.items()
        if name not in _EXCLUDED_FIELDS and (t in _VOCABULARY_TYPES or name in _VRH_OBJECT_FIELDS)
    }


def build_table(
    surface: dict[str, str],
    meta: dict[str, dict],
    descriptions: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Merge the schema-derived vocabulary ``surface`` with hand-pinned ``meta`` and
    the schema's ``descriptions`` into the committed
    ``{field: {unit, origin, desc, rankable}}`` table.

    Units and XC-functional origins are not in the schema, so they're supplied by
    hand per field. ``origin`` may be ``None`` (a field with no DFT functional, e.g.
    a count), but that must be an explicit decision: a field *absent* from ``meta``
    is a lockstep gap (its values would silently lose their unit and XC functional),
    so the table fails loudly rather than ship incomplete.

    ``desc`` is the opposite kind of value: it *is* in the schema, but only for some
    fields. It's optional — a missing description is ``None`` (the adapter layers a
    curated override on top for the load-bearing blanks), never a lockstep error.

    ``rankable`` is derived from the field's scalar type: a boolean flag (``is_metal``,
    ``is_stable``, ``is_magnetic``, ``is_gap_direct``) is a hard *filter*, never a
    ranking target — scoring it gives every survivor the same desirability (they all
    passed the filter), a meaningless flat score. Booleans stay in the vocabulary (as
    filter predicates) but are marked ``rankable=False`` so the ranking stage can reject
    them as targets."""
    missing = sorted(name for name in surface if name not in meta)
    if missing:
        raise ValueError(
            f"vocabulary fields missing hand-pinned unit/origin metadata: {', '.join(missing)}. "
            "Add an entry (unit/origin may be None, but the decision must be explicit) "
            "to keep the FIELD_UNITS/_FIELD_ORIGIN lockstep."
        )
    descriptions = descriptions or {}
    return {
        name: {
            **meta[name],
            "desc": descriptions.get(name),
            "rankable": surface[name] != "boolean",
        }
        for name in surface
    }


def parse_query_params(openapi: dict) -> set[str]:
    """The set of query-parameter names the ``/materials/summary/`` GET endpoint
    declares — the *pushable* surface.

    This is a DISTINCT, larger surface than the retrievable ``SummaryDoc`` fields:
    a field can be returned but not filtered on (``is_magnetic`` is retrievable yet
    not a query param — pushing it 400s), and a field's filter param can be renamed
    (the elastic moduli filter via ``k_vrh_min``/``g_vrh_min``, not
    ``bulk_modulus_min``). The adapter gates what it pushes on membership in this
    set, so a param MP would reject or silently ignore never reaches the wire."""
    operation = openapi["paths"]["/materials/summary/"]["get"]
    return {param["name"] for param in operation.get("parameters", [])}


def parse_summary_fields(openapi: dict) -> dict[str, str]:
    """Map each ``SummaryDoc`` property name to its scalar type category.

    Walks the ``SummaryDoc`` schema in an OpenAPI document and classifies every
    field by the scalar type the generator will later filter on (numeric/boolean
    fields make the vocabulary surface; strings/arrays/untyped identity fields are
    dropped downstream). Fields with no resolvable scalar type are omitted."""
    props = openapi["components"]["schemas"]["SummaryDoc"]["properties"]
    return {
        name: scalar for name, prop in props.items() if (scalar := _scalar_type(prop)) is not None
    }


def parse_summary_descriptions(openapi: dict) -> dict[str, str]:
    """Map each ``SummaryDoc`` property that carries a ``description`` to its one-line
    text.

    Unlike unit/origin (hand-pinned tribal knowledge), the schema *does* ship a short
    gloss for many fields (``band_gap`` -> "Band gap energy in eV."). Surfacing it lets
    the vocabulary handed to the LLM say what a field *means*, not just its unit — the
    fix for wrong-but-plausible proxy picks (an ``eV`` field grabbed as a "voltage").
    Fields with no description are omitted; the adapter layers curated overrides on top
    for the load-bearing ones the schema leaves blank (e.g. ``vbm``)."""
    props = openapi["components"]["schemas"]["SummaryDoc"]["properties"]
    return {name: prop["description"] for name, prop in props.items() if prop.get("description")}


def generate_table(openapi: dict) -> dict[str, dict]:
    """Run the full pipeline on an OpenAPI doc: parse → vocabulary surface → merge
    with ``_FIELD_META`` (and the schema descriptions) into the committed
    ``{field: {unit, origin, desc, rankable}}`` table.

    Raises (via the build_table lockstep guard) if the schema exposes a vocabulary
    field with no hand-pinned metadata — the signal to update ``_FIELD_META``."""
    return build_table(
        vocabulary_fields(parse_summary_fields(openapi)),
        _FIELD_META,
        parse_summary_descriptions(openapi),
    )


def render_module(table: dict[str, dict], params: set[str], *, source: str) -> str:
    """Render the committed ``_mp_fields.py`` source (sorted for a stable diff): the
    retrievable ``MP_FIELDS`` table plus the ``PUSHABLE_PARAMS`` filter surface. The
    module is the single artifact the adapter imports."""
    lines = [
        '"""Materials Project field table — GENERATED, do not edit by hand.',
        "",
        f"Regenerate with: python tools/gen_mp_vocab.py  (source: {source})",
        "",
        "MP_FIELDS maps each retrievable SummaryDoc property to its pinned unit (None =",
        "dimensionless/count), XC-functional origin (None = no traceable functional),",
        "schema description (None = the schema ships no gloss; a curated override fills the",
        "load-bearing blanks), and rankable (False = a boolean flag — a hard filter, never",
        "a ranking target). PUSHABLE_PARAMS is the /summary GET query-param surface — the",
        "names the adapter is allowed to push server-side (distinct from, and larger than,",
        "MP_FIELDS).",
        '"""',
        "",
        "MP_FIELDS: dict[str, dict] = {",
    ]
    for name in sorted(table):
        entry = table[name]
        unit, origin = _pylit(entry["unit"]), _pylit(entry["origin"])
        desc = entry.get("desc")
        desc_lit = "None" if desc is None else json.dumps(desc, ensure_ascii=False)
        rankable = entry.get("rankable", True)
        lines.append(
            f'    "{name}": {{"unit": {unit}, "origin": {origin}, '
            f'"desc": {desc_lit}, "rankable": {rankable}}},'
        )
    lines.append("}")
    lines.append("")
    lines.append("PUSHABLE_PARAMS: frozenset[str] = frozenset(")
    lines.append("    {")
    for name in sorted(params):
        lines.append(f'        "{name}",')
    lines.append("    }")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _pylit(value: str | None) -> str:
    """Render a unit/origin as a ruff-clean Python literal: a double-quoted string or
    ``None``. Keeps regeneration idempotent (no quote-only diff to reformat)."""
    return "None" if value is None else f'"{value}"'


def main() -> None:
    """Regenerate the committed field table from the vendored schema."""
    openapi = json.loads(_VENDORED_SCHEMA.read_text())
    table = generate_table(openapi)
    params = parse_query_params(openapi)
    source = openapi.get("_api_version") or openapi.get("_source") or "vendored schema"
    _OUTPUT.write_text(render_module(table, params, source=source))
    print(f"wrote {_OUTPUT} ({len(table)} fields, {len(params)} pushable params)")


if __name__ == "__main__":
    main()
