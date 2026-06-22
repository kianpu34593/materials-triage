"""Tests for the model-selection policy (part of the auth-tier story, task #4).

The policy is the deterministic rule behind the product requirement: an anonymous
visitor is pinned to a single fixed model (so the platform's bill is predictable),
while a signed-in user may choose any model the platform offers. It is pure logic
— no FastAPI, no AWS — so the web layer can lean on it being correct.
"""

import pytest
from mt_server.policy import resolve_model


def test_anonymous_user_is_pinned_to_the_fixed_default_model():
    """An anonymous request gets the fixed default model — the model is not
    user-selectable for anon, which is what keeps the shared-account bill bounded."""
    chosen = resolve_model(
        "anon", None, default="model-default", allowed=frozenset({"model-a", "model-b"})
    )
    assert chosen == "model-default"


def test_signed_in_user_gets_the_allowed_model_they_request():
    """A signed-in user may choose any model the platform offers — the requested
    model is honored when it is in the allowed set."""
    chosen = resolve_model(
        "user", "model-b", default="model-default", allowed=frozenset({"model-a", "model-b"})
    )
    assert chosen == "model-b"


def test_signed_in_user_requesting_an_unoffered_model_is_rejected():
    """A model the platform does not offer is refused rather than silently
    swapped — the caller learns its choice was invalid."""
    with pytest.raises(ValueError, match="model-x"):
        resolve_model(
            "user", "model-x", default="model-default", allowed=frozenset({"model-a", "model-b"})
        )


def test_signed_in_user_without_a_request_falls_back_to_the_default():
    """No explicit choice is not an error — a signed-in user who does not pick a
    model gets the platform default, same as anon."""
    chosen = resolve_model(
        "user", None, default="model-default", allowed=frozenset({"model-a", "model-b"})
    )
    assert chosen == "model-default"


def test_unknown_tier_is_rejected():
    """Only the known tiers are honored; an unrecognized tier is a programming
    error, not a silent fall-through to user privileges."""
    with pytest.raises(ValueError, match="tier"):
        resolve_model("admin", "model-a", default="model-default", allowed=frozenset({"model-a"}))


def test_anonymous_request_for_another_model_is_silently_pinned():
    """An anon request naming a different model is not an error — it is pinned to
    the default (the UI greys the selector out anyway)."""
    chosen = resolve_model(
        "anon", "model-a", default="model-default", allowed=frozenset({"model-a", "model-b"})
    )
    assert chosen == "model-default"
