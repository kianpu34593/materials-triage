"""Tests for the dev-only MP vocabulary generator (``tools/gen_mp_vocab.py``).

The generator parses the Materials Project OpenAPI schema *offline* — against a
faithful fixture sliced from the live ``/openapi.json`` — to derive the vocabulary
``SummaryDoc`` field surface. It runs at build time; its committed output feeds
the adapter's ``property_vocabulary``. Keeping the schema source vendored (not a
live runtime fetch) keeps every triage run replayable.
"""

import json
from pathlib import Path

import pytest
from gen_mp_vocab import build_table, parse_summary_fields, vocabulary_fields

FIXTURE = Path(__file__).parent / "fixtures" / "mp_openapi_summary.json"


def test_parser_classifies_summary_fields_by_scalar_type():
    """The parser walks ``SummaryDoc.properties`` and resolves each field's scalar
    type through the nullable ``anyOf`` wrapper MP wraps every optional field in,
    so the generator can later keep only the filterable/rankable surface."""
    fields = parse_summary_fields(json.loads(FIXTURE.read_text()))

    assert fields["band_gap"] == "number"  # anyOf[number, null] -> number
    assert fields["is_stable"] == "boolean"  # plain boolean (no anyOf wrapper)
    assert fields["nelements"] == "integer"  # anyOf[integer, null] -> integer


def test_vocabulary_fields_keeps_numeric_and_boolean_drops_the_rest():
    """The vocabulary the LLM may name is the filterable/rankable surface: numeric
    and boolean fields survive; identity (untyped material_id), free-text strings,
    and array fields are dropped — a hypothesis can't sensibly constrain on those."""
    surface = vocabulary_fields(parse_summary_fields(json.loads(FIXTURE.read_text())))

    assert {"band_gap", "density", "nelements", "is_metal", "is_stable"} <= set(surface)
    assert "material_id" not in surface  # untyped identity
    assert "formula_pretty" not in surface  # free-text string
    assert "possible_species" not in surface  # array


def test_vocabulary_fields_keeps_vrh_moduli_but_not_composition_objects():
    """bulk_modulus / shear_modulus are object-typed in the schema (a {voigt, reuss,
    vrh} dict the adapter collapses to the VRH scalar), so they belong in the
    vocabulary. The schema can't tell them apart from composition / composition_reduced
    (also object-of-number, but element->amount maps, not scalars) — so the two
    collapsible moduli are kept by an explicit allowlist, the rest stay dropped."""
    surface = vocabulary_fields(parse_summary_fields(json.loads(FIXTURE.read_text())))

    assert "bulk_modulus" in surface
    assert "shear_modulus" in surface
    assert "composition" not in surface
    assert "composition_reduced" not in surface


def test_build_table_merges_unit_and_origin_per_field():
    """The committed table pairs each vocabulary field with its hand-pinned unit and
    XC-functional origin (neither is in the schema). A dimensionless count carries an
    explicit unit=None / origin=None — a field with no DFT functional, decided on
    purpose, not forgotten."""
    surface = {"band_gap": "number", "nelements": "integer"}
    meta = {
        "band_gap": {"unit": "eV", "origin": "electronic_structure"},
        "nelements": {"unit": None, "origin": None},
    }

    table = build_table(surface, meta)

    assert table["band_gap"] == {"unit": "eV", "origin": "electronic_structure"}
    assert table["nelements"] == {"unit": None, "origin": None}


def test_build_table_rejects_a_vocabulary_field_missing_from_metadata():
    """The lockstep guard: a field the schema put in the vocabulary but the hand
    metadata never pinned is a unit/origin gap (its retrieved values would silently
    lose their unit and XC functional). build_table fails loudly, naming the field,
    instead of emitting an incomplete table."""
    surface = {"band_gap": "number", "efermi": "number"}
    meta = {"band_gap": {"unit": "eV", "origin": "electronic_structure"}}  # efermi forgotten

    with pytest.raises(ValueError, match="efermi"):
        build_table(surface, meta)
