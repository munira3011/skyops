from typing import Literal

from pydantic import BaseModel

_CATEGORIES = (
    "prompt_injection",
    "abusive_or_toxic",
    "pii_or_social_engineering",
    "internal_leak",
    "none",
)


class GuardrailVerdict(BaseModel):
    """Structured verdict from an LLM guardrail check - shared shape for input_guard and
    output_guard so both can use `gateway.client.chat_structured` instead of parsing free text."""

    flagged: bool
    category: Literal[*_CATEGORIES]
    reason: str
