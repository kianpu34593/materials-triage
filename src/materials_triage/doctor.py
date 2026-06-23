"""``materials-triage doctor`` — a credential/environment self-check.

Mirrors the convenience of a one-command setup check: it reports, with a ✓/✗
checklist, whether the credentials and optional dependencies the live pipeline
needs are in place, so a first-time user learns what is missing *before* a real
run fails midway. The probing seams (environment, AWS-credential resolution) are
injected so the core is pure and offline-testable.
"""

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class Check:
    """One environment check: a human ``name``, whether it passed (``ok``), a
    ``detail`` line, and whether failing it should block a live run (``required``)."""

    name: str
    ok: bool
    detail: str
    required: bool


def _default_aws_probe() -> bool:
    """Whether botocore can resolve AWS credentials (for Bedrock). Lazy-imports
    botocore so the module imports without the optional ``llm`` dependency."""
    try:
        import botocore.session
    except ImportError:
        return False
    return botocore.session.get_session().get_credentials() is not None


def run_doctor(
    env: Mapping[str, str],
    *,
    aws_creds_present: Callable[[], bool] = _default_aws_probe,
    out: TextIO | None = None,
) -> int:
    """Print the environment checklist to ``out`` (stdout by default) and return a
    process exit code: 0 when every *required* check passes, 1 otherwise."""
    out = out if out is not None else sys.stdout
    checks = check_environment(env, aws_creds_present=aws_creds_present)
    print(format_report(checks), file=out)
    return 0 if all(c.ok for c in checks if c.required) else 1


def format_report(checks: tuple[Check, ...]) -> str:
    """Render ``checks`` as a ✓/✗ checklist, one line each, with the detail.

    A passing check is marked ✓; a failing *required* check ✗; a failing
    *optional* check ⚠ (a warning, not a blocker)."""
    lines = []
    for c in checks:
        mark = "✓" if c.ok else ("✗" if c.required else "⚠")
        lines.append(f"{mark} {c.name}: {c.detail}")
    return "\n".join(lines)


def check_environment(
    env: Mapping[str, str],
    *,
    aws_creds_present: Callable[[], bool] = _default_aws_probe,
) -> tuple[Check, ...]:
    """Return the environment checks for ``env`` (a credential mapping).

    ``aws_creds_present`` is an injected probe for AWS/Bedrock credential
    resolution; the default uses botocore, but tests pass a fake so the core is
    pure and offline-testable. Reads only the supplied ``env`` otherwise.
    """
    mp_key = env.get("X_API_KEY", "")
    mailto = env.get("OPENALEX_MAILTO", "")
    aws_ok = aws_creds_present()
    return (
        Check(
            name="Materials Project",
            ok=bool(mp_key),
            detail="X_API_KEY is set" if mp_key else "X_API_KEY is not set",
            required=True,
        ),
        Check(
            name="AWS / Bedrock",
            ok=aws_ok,
            detail="AWS credentials resolve" if aws_ok else "AWS credentials do not resolve",
            required=True,
        ),
        Check(
            name="OpenAlex polite pool",
            ok=bool(mailto),
            detail=(
                f"OPENALEX_MAILTO is set ({mailto})"
                if mailto
                else "OPENALEX_MAILTO is not set (literature search still works, "
                "but without the faster polite pool)"
            ),
            required=False,
        ),
    )
