"""Input policy gate — workflow step 1.

Classifies a request as in-scope materials-triage vs forbidden/out-of-scope and
returns a :class:`GateDecision`. The gate is **deterministic** in v1 (no LLM), so
it is injection-resistant by construction and the allowlist cannot be widened by
the input text. The same gate covers both input surfaces — the initial query and
later manual spec-field edits.

The caller (orchestrator) decides what to do with a refusal: a forbidden request
is logged and refused, and is *not* recorded as a ``TriageRun``.
"""

from pydantic import BaseModel, ConfigDict


class GateDecision(BaseModel):
    """The gate's verdict on one piece of input text.

    Immutable: a verdict, once reached, travels unchanged to the caller.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str = ""
    category: str | None = None


# Forbidden-action denylist: phrases that name a capability the agent does not
# (and must not) have — physical wet-lab actions, private/proprietary lab data,
# or scraping closed/paywalled sources. This is a deterministic, best-effort fast
# path that yields a cheap, certain, logged refusal *before any LLM call*. It is
# NOT the safety guarantee (capability-by-construction is; see ADR 0004) — the
# fuzzy "is this even a materials request?" scope decision lives in the LLM's role
# system prompt, not here. Each entry is ``(category, reason, trigger phrases)``;
# the first matching category wins.
_FORBIDDEN_ACTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "wet_lab",
        "Request names a physical wet-lab action; no capability exists to comply.",
        ("synthesize", "synthesise", "in the lab", "cv scan"),
    ),
    (
        "private_data",
        "Request reaches for private/proprietary lab data; only public sources are allowed.",
        ("internal lab", "unpublished", "private database", "proprietary", "confidential"),
    ),
    (
        "paywalled",
        "Request asks to scrape a closed/paywalled source; only open public sources are allowed.",
        ("scrape", "paywall", "paywalled", "closed source", "behind the paywall"),
    ),
)


def check_input(text: str) -> GateDecision:
    """Return the gate's verdict for ``text`` (a query or a manual spec field)."""
    lowered = text.lower()
    for category, reason, terms in _FORBIDDEN_ACTIONS:
        if any(term in lowered for term in terms):
            return GateDecision(allowed=False, category=category, reason=reason)
    return GateDecision(allowed=True, category="in_scope")
