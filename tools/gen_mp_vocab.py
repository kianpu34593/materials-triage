"""Dev-only generator for the Materials Project property vocabulary.

Parses the MP ``/openapi.json`` schema *at build time* and emits a committed
field-table the adapter's ``property_vocabulary`` reads. The schema is vendored,
never fetched at runtime, so every triage run stays replayable (see ADR/handoff).

Run as a script to regenerate the table; the pure parsing functions here are
exercised offline against a fixture sliced from the live schema.
"""


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

#: Object-typed fields the adapter collapses to a single scalar, so they belong in
#: the vocabulary despite their ``object`` type. MP returns the elastic moduli as a
#: Voigt-Reuss-Hill dict ``{voigt, reuss, vrh}``; the adapter's ``_scalar`` takes the
#: VRH average. The schema can't distinguish these from ``composition`` /
#: ``composition_reduced`` (also object-of-number, but element->amount maps), so they
#: must be named explicitly — mirroring the exact fields ``_scalar`` already handles.
_VRH_OBJECT_FIELDS = frozenset({"bulk_modulus", "shear_modulus"})


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
        if t in _VOCABULARY_TYPES or name in _VRH_OBJECT_FIELDS
    }


def build_table(surface: dict[str, str], meta: dict[str, dict]) -> dict[str, dict]:
    """Merge the schema-derived vocabulary ``surface`` with hand-pinned ``meta`` into
    the committed ``{field: {unit, origin}}`` table.

    Units and XC-functional origins are not in the schema, so they're supplied by
    hand per field. ``origin`` may be ``None`` (a field with no DFT functional, e.g.
    a count), but that must be an explicit decision: a field *absent* from ``meta``
    is a lockstep gap (its values would silently lose their unit and XC functional),
    so the table fails loudly rather than ship incomplete."""
    missing = sorted(name for name in surface if name not in meta)
    if missing:
        raise ValueError(
            f"vocabulary fields missing hand-pinned unit/origin metadata: {', '.join(missing)}. "
            "Add an entry (unit/origin may be None, but the decision must be explicit) "
            "to keep the FIELD_UNITS/_FIELD_ORIGIN lockstep."
        )
    return {name: dict(meta[name]) for name in surface}


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
