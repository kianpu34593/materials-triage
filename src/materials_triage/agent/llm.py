"""The Bedrock-backed LLM providers (hypothesis and synthesis).

Each turns a rendered prompt into a validated domain model
(:class:`~materials_triage.core.hypothesis.Hypothesis` /
:class:`~materials_triage.core.synthesis.Synthesis`). The Bedrock call is injected
(``complete``) so the providers are exercised fully offline; the real transport is
built lazily only when a provider actually goes to Bedrock — mirroring the
MaterialsProjectAdapter's ``http_get`` seam.
"""

from collections.abc import Callable

from materials_triage.agent.prompts import ROLE_SYSTEM_PROMPT
from materials_triage.core.hypothesis import Hypothesis
from materials_triage.core.synthesis import Synthesis

#: A completion seam: a rendered prompt string -> a validated Hypothesis.
Complete = Callable[[str], Hypothesis]
#: A completion seam: a rendered prompt string -> a validated Synthesis.
CompleteSynthesis = Callable[[str], Synthesis]
#: A completion seam: a rendered prompt string -> a plain text response.
CompleteText = Callable[[str], str]


def _role_messages(prompt: str) -> list[tuple[str, str]]:
    """Render chat messages with the role in the system slot and ``prompt`` as the
    human content, so every real Bedrock call carries the role / trust-boundary
    directive (Layer 3) and no call site can forget it."""
    return [("system", ROLE_SYSTEM_PROMPT), ("human", prompt)]


#: Defaults for the real Bedrock transport. The model id is an AWS Bedrock
#: inference-profile id for Claude; confirm it against the target account before
#: running the live path (offline tests never touch it).
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-1"


class HypothesisProvider:
    """Produce a Hypothesis from a rendered prompt via an injected Bedrock seam."""

    def __init__(
        self,
        complete: Complete | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = DEFAULT_REGION,
    ) -> None:
        # Injecting `complete` is the offline mode (tests pass a fake); the default
        # is the real Bedrock transport, built lazily so importing this module
        # (and every offline test) never needs langchain_aws or AWS credentials.
        self._complete = complete or _bedrock_complete(model_id, region)

    def propose(self, prompt: str) -> Hypothesis:
        return self._complete(prompt)


class SynthesisProvider:
    """Produce a Synthesis (grounded narrative) from a rendered prompt via an
    injected Bedrock seam — the same offline-testable pattern as
    HypothesisProvider, structured to the Synthesis schema."""

    def __init__(
        self,
        complete: CompleteSynthesis | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._complete = complete or _bedrock_structured(model_id, region, Synthesis)

    def synthesize(self, prompt: str) -> Synthesis:
        return self._complete(prompt)


class QueryProvider:
    """Turn a rendered prompt into a plain-text literature search query via an
    injected Bedrock seam — the same offline-testable pattern, unstructured text
    out. Used to rewrite the user goal into a focused query before RAG."""

    def __init__(
        self,
        complete: CompleteText | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._complete = complete or _bedrock_text(model_id, region)

    def rewrite_query(self, prompt: str) -> str:
        return self._complete(prompt)


def _bedrock_text(model_id: str, region: str) -> CompleteText:
    """Build a real plain-text completion seam (no structured schema). Returns the
    response's text content. ``langchain_aws`` is imported only on invocation."""

    def complete(prompt: str) -> str:
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(model=model_id, region_name=region)
        response = model.invoke(_role_messages(prompt))
        return str(response.content).strip()

    return complete


def _bedrock_complete(model_id: str, region: str) -> Complete:
    """Build the real Hypothesis completion seam (kept as a thin named wrapper so
    the existing live smoke test and call sites are unchanged)."""
    return _bedrock_structured(model_id, region, Hypothesis)


def _bedrock_structured(model_id: str, region: str, schema: type):
    """Build a real completion seam structured to ``schema``. ``langchain_aws`` is
    imported only when the seam is actually invoked, so construction and offline
    use never require it (or AWS credentials)."""

    def complete(prompt: str):
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(model=model_id, region_name=region)
        return model.with_structured_output(schema).invoke(_role_messages(prompt))

    return complete
