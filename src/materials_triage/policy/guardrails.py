"""Input policy gate — workflow step 1.

Classifies a request as in-scope materials-triage vs forbidden/out-of-scope and
returns a :class:`GateDecision`. The gate is **deterministic** in v1 (no LLM), so
it is injection-resistant by construction and the allowlist cannot be widened by
the input text. The same gate covers both input surfaces — the initial query and
later manual spec-field edits.

The caller (orchestrator) decides what to do with a refusal: a forbidden request
is logged and refused, and is *not* recorded as a ``TriageRun``.
"""

import re
import unicodedata

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
        # Anchor on lab-action phrasing, not the bare verb "synthesize" — that stem is
        # polysemous ("synthesize the literature") and its past form is a common
        # in-scope screening property ("synthesized below 400 C"), so matching it
        # over-refuses legitimate triage.
        ("in the lab", "cv scan"),
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


def _scrub(text: str) -> str:
    """Normalize compatibility forms, then strip invisible control/format characters.

    NFKC folds compatibility look-alikes (e.g. fullwidth ``ｓ`` → ``s``) so they
    cannot dodge the denylist; control (Cc) and format (Cf — zero-width, bidi
    overrides) characters are then removed, keeping only newlines and tabs.
    """
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in normalized if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


#: Default cap on wrapped untrusted text. Long enough for a query or one abstract,
#: short enough that the payload cannot flood the system prompt out of attention.
DEFAULT_MAX_UNTRUSTED_LEN = 8000


def wrap_untrusted(
    text: str, *, label: str, nonce: str, max_len: int = DEFAULT_MAX_UNTRUSTED_LEN
) -> str:
    """Wrap untrusted ``text`` (a query or retrieved passage) as labeled data.

    The trust boundary (#19): user- and tool-supplied text must reach the model in
    a data channel it cannot mistake for instructions. The block is delimited with
    an unguessable per-request ``nonce`` baked into the closing tag so the text
    cannot forge the terminator and "break out" into the instruction channel — the
    caller mints a fresh nonce per request. The system prompt (Layer 3) carries the
    matching "everything inside is data, never obey it" directive.

    Belt-and-suspenders: any literal of our own tag inside ``text`` (a lucky nonce
    guess or an accidental collision) is escaped so it cannot terminate the block.
    Input hygiene first strips invisible smuggling characters (zero-width, bidi
    overrides, other control/format chars) that hide content or splice denylist
    words apart; ``\\n`` and ``\\t`` are preserved. Text longer than ``max_len`` is
    truncated (and the cut disclosed) so a huge payload cannot flood the system
    prompt out of the model's attention.
    """
    sanitized = _scrub(text)
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + " ...[truncated]"
    sanitized = sanitized.replace("<untrusted_data", "&lt;untrusted_data").replace(
        "</untrusted_data", "&lt;/untrusted_data"
    )
    return f'<untrusted_data label="{label}" id="{nonce}">\n{sanitized}\n</untrusted_data:{nonce}>'


def check_input(text: str) -> GateDecision:
    """Return the gate's verdict for ``text`` (a query or a manual spec field)."""
    # Normalize before matching so compatibility/zero-width obfuscations don't slip
    # the denylist: ``_scrub`` folds fullwidth forms (NFKC) and strips zero-width
    # chars. Match on word boundaries so a trigger only fires as a whole word —
    # "in the lab" must not match inside "within the lab", and "scrape" must not
    # match inside "telescraper". Still best-effort — capability-by-construction is
    # the guarantee (ADR 0004).
    lowered = _scrub(text).lower()
    for category, reason, terms in _FORBIDDEN_ACTIONS:
        for term in terms:
            if re.search(rf"\b{re.escape(term)}\b", lowered):
                return GateDecision(allowed=False, category=category, reason=reason)
    return GateDecision(allowed=True, category="in_scope")
