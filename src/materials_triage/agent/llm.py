"""The Bedrock-backed hypothesis provider.

Turns a rendered prompt into a validated :class:`~materials_triage.core.hypothesis.Hypothesis`.
The Bedrock call is injected (``complete``) so the provider is exercised fully
offline; the real transport is built lazily only when the provider actually goes
to Bedrock — mirroring the MaterialsProjectAdapter's ``http_get`` seam.
"""

from collections.abc import Callable

from materials_triage.agent.prompts import KEYWORD_EXTRACTION_SYSTEM_PROMPT, ROLE_SYSTEM_PROMPT
from materials_triage.core.hypothesis import Hypothesis
from materials_triage.core.synthesis import Synthesis

#: A completion seam: a rendered prompt string -> a validated Hypothesis.
Complete = Callable[[str], Hypothesis]

#: A keyword-extraction seam: a free-text goal -> a literature search query string.
ExtractKeywords = Callable[[str], str]

#: A synthesis seam: a rendered prompt string -> a validated Synthesis.
Synthesize = Callable[[str], Synthesis]


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
        extract: ExtractKeywords | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = DEFAULT_REGION,
    ) -> None:
        # Injecting `complete`/`extract` is the offline mode (tests pass fakes); the
        # defaults are the real Bedrock transports, built lazily so importing this
        # module (and every offline test) never needs langchain_aws or AWS credentials.
        self._complete = complete or _bedrock_complete(model_id, region)
        self._extract = extract or _bedrock_extract_keywords(model_id, region)

    def propose(self, prompt: str) -> Hypothesis:
        return self._complete(prompt)

    def extract_keywords(self, goal: str) -> str:
        """Distill a free-text goal into a literature search query for the RAG step."""
        return self._extract(goal)


class SynthesisProvider:
    """Produce a Synthesis from a rendered prompt via an injected Bedrock seam.

    Mirrors :class:`HypothesisProvider`: the seam is injected for offline tests and
    built lazily for the real path, so importing this module never needs langchain_aws
    or AWS credentials."""

    def __init__(
        self,
        synthesize: Synthesize | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._synthesize = synthesize or _bedrock_synthesize(model_id, region)

    def synthesize(self, prompt: str) -> Synthesis:
        return self._synthesize(prompt)


def _bedrock_complete(model_id: str, region: str) -> Complete:
    """Build the real completion seam. ``langchain_aws`` is imported only when the
    seam is actually invoked, so construction and offline use never require it.
    """

    def complete(prompt: str) -> Hypothesis:
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(model=model_id, region_name=region)
        return model.with_structured_output(Hypothesis).invoke(_role_messages(prompt))

    return complete


def _bedrock_synthesize(model_id: str, region: str) -> Synthesize:
    """Build the real synthesis seam. Like ``_bedrock_complete``, imports
    ``langchain_aws`` only when invoked; the rendered synthesis prompt rides the human
    slot under ROLE_SYSTEM_PROMPT, and structured output validates to a Synthesis."""

    def synthesize(prompt: str) -> Synthesis:
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(model=model_id, region_name=region)
        return model.with_structured_output(Synthesis).invoke(_role_messages(prompt))

    return synthesize


def _bedrock_extract_keywords(model_id: str, region: str) -> ExtractKeywords:
    """Build the real keyword-extraction seam. Like ``_bedrock_complete``, imports
    ``langchain_aws`` only when invoked, so construction stays offline. The goal is
    fenced as untrusted DATA (fresh per-call nonce) under a keyword-extraction system
    prompt; the model's plain-text reply is the literature search query."""

    def extract(goal: str) -> str:
        import secrets

        from langchain_aws import ChatBedrockConverse

        from materials_triage.policy.guardrails import wrap_untrusted

        model = ChatBedrockConverse(model=model_id, region_name=region)
        wrapped = wrap_untrusted(goal, label="user goal", nonce=secrets.token_hex(8))
        response = model.invoke([("system", KEYWORD_EXTRACTION_SYSTEM_PROMPT), ("human", wrapped)])
        content = response.content
        return content if isinstance(content, str) else str(content)

    return extract
