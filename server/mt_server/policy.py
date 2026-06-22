"""Model-selection policy (task #4).

The deterministic rule behind the product requirement: an anonymous visitor is
pinned to one fixed model (predictable shared-account bill); a signed-in user may
choose any model the platform offers. Pure logic — no FastAPI, no AWS — so the web
layer can lean on it being correct.
"""

#: The recognized account tiers. ``anon`` is the unauthenticated visitor (pinned
#: to the fixed model); ``user`` is a signed-in account (may pick a model).
TIERS = frozenset({"anon", "user"})


def resolve_model(tier, requested, *, default, allowed):
    """Return the model id to use for a request.

    ``default`` is the platform's fixed model; ``allowed`` is the set a signed-in
    user may choose from. An anon request is pinned to ``default`` (any requested
    model is ignored); a signed-in user gets their requested model when it is
    offered, the default when they request none, and a ``ValueError`` when they
    request a model that is not offered. An unrecognized ``tier`` is a
    ``ValueError`` rather than a silent fall-through.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")
    if tier == "anon":
        return default
    if requested is None:
        return default
    if requested not in allowed:
        raise ValueError(f"model {requested!r} is not offered; choose one of {sorted(allowed)}")
    return requested
