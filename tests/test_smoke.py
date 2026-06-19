"""Smoke test: the package imports and exposes its version.

Placeholder so CI has something to collect until real triage logic lands.
"""

import materials_triage


def test_package_exposes_version():
    assert materials_triage.__version__ == "0.0.1"
