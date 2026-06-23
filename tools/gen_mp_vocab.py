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


def parse_summary_fields(openapi: dict) -> dict[str, str]:
    """Map each ``SummaryDoc`` property name to its scalar type category.

    Walks the ``SummaryDoc`` schema in an OpenAPI document and classifies every
    field by the scalar type the generator will later filter on (numeric/boolean
    fields make the queryable surface; strings/arrays/untyped identity fields are
    dropped downstream). Fields with no resolvable scalar type are omitted."""
    props = openapi["components"]["schemas"]["SummaryDoc"]["properties"]
    return {
        name: scalar for name, prop in props.items() if (scalar := _scalar_type(prop)) is not None
    }
