import re
from typing import Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.gateway.client import GatewayError, chat_structured
from app.graph.state import AgentState

_ROUTES = ("flight_status_agent", "rag_policy_agent", "booking_agent", "booking_disruption_agent")


class _RouteDecision(BaseModel):
    """Structured reply schema for `_llm_classify` - constrains the LLM to one of `_ROUTES` (or
    "none") via tool-calling, instead of parsing a freeform string reply."""

    route: Literal[*_ROUTES, "none"]


_CLASSIFIER_PROMPT = SystemMessage(
    content=(
        "You are an intent router for Zenith Air's customer assistant. Read the user's "
        "latest message and classify it into exactly one of:\n"
        "- flight_status_agent - questions about a flight's status, delay, gate, or cancellation "
        "(not tied to a specific passenger's booking)\n"
        "- booking_agent - a passenger asking about their OWN booking/reservation details by "
        "PNR - cabin class, seat, fare type, baggage allowance, loyalty status, booking "
        "confirmation. Not about a delay/cancellation impact.\n"
        "- booking_disruption_agent - a passenger with a specific booking (PNR) asking what "
        "happens to THEM because their flight was delayed/cancelled - rebooking, compensation, "
        "refund eligibility for their own trip\n"
        "- rag_policy_agent - general questions about baggage, loyalty tiers, check-in, or "
        "policy rules that aren't about a specific booking\n"
        "- none - anything else"
    )
)

# Deterministic fallback used only if the gateway/LiteLLM proxy is unavailable.
_FLIGHT_STATUS_KEYWORDS = ("flight status", "delayed", "delay", "on time", "gate", "cancelled")
_DISRUPTION_KEYWORDS = (
    "rebook", "rebooking", "compensation", "travel credit", "disrupted", "disruption",
)
_BOOKING_KEYWORDS = (
    "my booking", "booking details", "my reservation", "my seat", "my cabin", "my fare",
    "booking confirmation", "what class am i",
)
_POLICY_KEYWORDS = (
    "baggage", "luggage", "checked bag", "allowance", "loyalty", "tier", "miles",
    "refund", "check-in", "checkin", "visa", "passport", "document", "policy",
)
_FLIGHT_NUMBER_RE = re.compile(r"\b[A-Z]{2}\d{3,4}\b")


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _keyword_classify(text: str) -> Optional[str]:
    lower = text.lower()
    if any(keyword in lower for keyword in _DISRUPTION_KEYWORDS):
        return "booking_disruption_agent"
    if any(keyword in lower for keyword in _BOOKING_KEYWORDS):
        return "booking_agent"
    if _FLIGHT_NUMBER_RE.search(text.upper()):
        return "flight_status_agent"
    if any(keyword in lower for keyword in _FLIGHT_STATUS_KEYWORDS):
        return "flight_status_agent"
    if any(keyword in lower for keyword in _POLICY_KEYWORDS):
        return "rag_policy_agent"
    return None


def _llm_classify(text: str) -> Optional[str]:
    decision = chat_structured([_CLASSIFIER_PROMPT, HumanMessage(content=text)], _RouteDecision)
    return decision.route if decision.route != "none" else None


def _classify(text: str) -> tuple[Optional[str], str]:
    """Returns (destination, handoff_reason). Tries LLM classification via the gateway first, falls back to keyword matching if the proxy is unreachable/misconfigured."""
    try:
        destination = _llm_classify(text)
        return destination, f"llm classified as {destination}" if destination else "llm classified as no match"
    except GatewayError as exc:
        destination = _keyword_classify(text)
        reason = f"gateway unavailable ({exc}), keyword fallback matched {destination}"
        return destination, reason if destination else f"gateway unavailable ({exc}), keyword fallback: no match"


def supervisor(state: AgentState) -> dict:
    """Reads the latest user message to route to a specialist; writes `next`/`active_agent`, or ends the turn once a specialist has already replied."""
    messages = state["messages"]
    if messages and isinstance(messages[-1], AIMessage):
        return {"next": None}

    destination, reason = _classify(_latest_user_text(state))
    if destination is None:
        reply = (
            "I can help with flight status, your booking details, disruption rebooking, or "
            "airline policy questions (baggage, loyalty tiers, check-in) - could you rephrase?"
        )
        return {"next": None, "messages": [AIMessage(content=reply)], "handoff_reason": reason}

    return {
        "next": destination,
        "active_agent": destination,
        "handoff_reason": reason,
    }
