"""Tests for the Bedrock-backed hypothesis provider in materials_triage.agent.llm.

The provider turns a rendered prompt into a validated Hypothesis. The Bedrock
call is injected (``complete``) so every test here runs fully offline, with no
network and no langchain/AWS dependency — mirroring the MaterialsProjectAdapter's
``http_get`` seam.
"""

import sys

import pytest

from materials_triage.agent.llm import HypothesisProvider, SynthesisProvider, _role_messages
from materials_triage.agent.prompts import ROLE_SYSTEM_PROMPT
from materials_triage.core.hypothesis import ConstraintProposal, Hypothesis
from materials_triage.core.schema import Constraint
from materials_triage.core.synthesis import Synthesis


def test_role_messages_prepends_system_role_to_human_prompt():
    """Every real Bedrock call carries the role in the system slot (Layer 3), so the
    role/trust-boundary directive is present on every call and cannot be forgotten by
    a call site; the rendered prompt is the human content."""
    messages = _role_messages("rendered hypothesis prompt")
    assert messages == [
        ("system", ROLE_SYSTEM_PROMPT),
        ("human", "rendered hypothesis prompt"),
    ]


def _canned_hypothesis() -> Hypothesis:
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0, max=4.0),
                rationale="wide-gap semiconductor maps to ~2-4 eV",
                confidence=0.7,
            ),
        ),
        mechanism="wide-gap stable oxides tend to ...",
    )


def test_propose_returns_the_hypothesis_the_seam_produced():
    """The provider delegates to its injected completion seam and hands back the
    Hypothesis it produced — so the rest of the pipeline can consume a validated
    Hypothesis without the provider inventing anything itself."""
    canned = _canned_hypothesis()
    provider = HypothesisProvider(complete=lambda prompt: canned)

    result = provider.propose("any rendered prompt")

    assert result is canned


def test_propose_forwards_the_prompt_to_the_seam_unchanged():
    """The provider passes the rendered prompt to the seam verbatim — no silent
    wrapping or prepending. Prompt wording (including the untrusted-data framing)
    is the prompts layer's responsibility, so the provider must not mutate it."""
    seen = []
    provider = HypothesisProvider(
        complete=lambda prompt: (seen.append(prompt), _canned_hypothesis())[1]
    )

    provider.propose("RENDERED PROMPT with untrusted DATA blocks")

    assert seen == ["RENDERED PROMPT with untrusted DATA blocks"]


def test_extract_keywords_returns_the_seam_output_and_forwards_the_goal():
    """The provider's keyword extraction (for the RAG step) delegates to its injected
    keyword seam and hands back what it produced, forwarding the goal verbatim — the
    provider invents no keywords itself."""
    seen = []
    provider = HypothesisProvider(
        complete=lambda prompt: _canned_hypothesis(),
        extract=lambda goal: (seen.append(goal), "wide band gap oxide photocatalyst")[1],
    )

    keywords = provider.extract_keywords("a stable wide-gap oxide for photocatalysis")

    assert keywords == "wide band gap oxide photocatalyst"
    assert seen == ["a stable wide-gap oxide for photocatalysis"]


def test_default_provider_constructs_offline_without_importing_bedrock():
    """Constructed with no injected seam, the provider builds its real Bedrock
    transport lazily: construction must not import langchain_aws or need AWS
    credentials, so the package imports and offline tests run on a box without
    langchain installed (mirrors the adapter's lazy `requests` transport)."""
    HypothesisProvider()

    assert "langchain_aws" not in sys.modules


def test_synthesis_provider_returns_the_synthesis_the_seam_produced():
    """The synthesis provider delegates to its injected seam and hands back the
    Synthesis it produced, forwarding the prompt verbatim — it invents nothing itself."""
    canned = Synthesis(summary="ZnO leads for a wide-gap photocatalyst.")
    seen = []
    provider = SynthesisProvider(synthesize=lambda prompt: (seen.append(prompt), canned)[1])

    result = provider.synthesize("RENDERED synthesis prompt")

    assert result is canned
    assert seen == ["RENDERED synthesis prompt"]


def test_synthesis_provider_constructs_offline_without_importing_bedrock():
    """Like the hypothesis provider, the synthesis provider builds its Bedrock seam
    lazily — construction needs no langchain_aws or AWS credentials."""
    SynthesisProvider()

    assert "langchain_aws" not in sys.modules


def _has_aws_credentials() -> bool:
    """True when botocore can resolve credentials from any source — environment
    variables, a named profile, or the shared ~/.aws/credentials file — without
    reading the secret itself (we only check that a credential set exists)."""
    try:
        import botocore.session
    except ImportError:
        return False
    return botocore.session.Session().get_credentials() is not None


@pytest.mark.live
@pytest.mark.skipif(
    not _has_aws_credentials(),
    reason="needs AWS credentials (env vars, a profile, or ~/.aws/credentials) for live Bedrock",
)
def test_live_propose_returns_a_real_hypothesis():
    """Smoke test against real Bedrock — deselected by default. With AWS creds set
    it exercises the lazy default seam end to end (ChatBedrockConverse +
    with_structured_output) and should return a schema-valid Hypothesis.

    Known flaky (~15% on a single call): the LLM occasionally emits malformed
    structured output that the Hypothesis schema correctly rejects — measured in a
    20-run stress test (stringified proposals list, flattened element_rule payload,
    a constraint with no bounds). That the gate rejects these is the intended
    safety property, not a bug; reliable conformance is the job of the retry loop
    deferred to the orchestrator (#23), so this single-shot smoke test stays strict
    and may fail on a given run. Re-run, or rely on the offline suite for CI."""
    provider = HypothesisProvider()

    hypothesis = provider.propose(
        "Propose spec constraints and ranking targets for finding a stable, "
        "wide-band-gap oxide semiconductor. Ground each proposal and cite sources."
    )

    assert isinstance(hypothesis, Hypothesis)
    assert len(hypothesis.proposals) >= 1
