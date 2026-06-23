"""Tests for the dev-only MP vocabulary generator (``tools/gen_mp_vocab.py``).

The generator parses the Materials Project OpenAPI schema *offline* — against a
faithful fixture sliced from the live ``/openapi.json`` — to derive the queryable
``SummaryDoc`` field surface. It runs at build time; its committed output feeds
the adapter's ``property_vocabulary``. Keeping the schema source vendored (not a
live runtime fetch) keeps every triage run replayable.
"""

import json
from pathlib import Path

from gen_mp_vocab import parse_summary_fields

FIXTURE = Path(__file__).parent / "fixtures" / "mp_openapi_summary.json"


def test_parser_classifies_summary_fields_by_scalar_type():
    """The parser walks ``SummaryDoc.properties`` and resolves each field's scalar
    type through the nullable ``anyOf`` wrapper MP wraps every optional field in,
    so the generator can later keep only the filterable/rankable surface."""
    fields = parse_summary_fields(json.loads(FIXTURE.read_text()))

    assert fields["band_gap"] == "number"  # anyOf[number, null] -> number
    assert fields["is_stable"] == "boolean"  # plain boolean (no anyOf wrapper)
    assert fields["nelements"] == "integer"  # anyOf[integer, null] -> integer
