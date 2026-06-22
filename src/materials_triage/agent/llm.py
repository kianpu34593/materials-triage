"""The Bedrock-backed hypothesis provider.

Turns a rendered prompt into a validated :class:`~materials_triage.core.hypothesis.Hypothesis`.
The Bedrock call is injected (``complete``) so the provider is exercised fully
offline; the real transport is built lazily only when the provider actually goes
to Bedrock — mirroring the MaterialsProjectAdapter's ``http_get`` seam.
"""

from collections.abc import Callable

from materials_triage.agent.prompts import ROLE_SYSTEM_PROMPT
from materials_triage.core.hypothesis import Hypothesis

#: A completion seam: a rendered prompt string -> a validated Hypothesis.
Complete = Callable[[str], Hypothesis]


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


def _bedrock_complete(model_id: str, region: str) -> Complete:
    """Build the real completion seam. ``langchain_aws`` is imported only when the
    seam is actually invoked, so construction and offline use never require it.
    """

    def complete(prompt: str) -> Hypothesis:
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(model=model_id, region_name=region)
        return model.with_structured_output(Hypothesis).invoke(_role_messages(prompt))

    return complete
