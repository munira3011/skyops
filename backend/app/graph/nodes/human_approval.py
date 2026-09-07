import logging

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.graph.state import AgentState

logger = logging.getLogger("skyops.human_approval")


def human_approval(state: AgentState) -> dict:
    """Reads `pending_approval`; interrupts the graph for a Zenith Air agent's approve/reject
    decision (resume value: `{"approved": bool, "agent_note": Optional[str]}`), then writes the
    outcome as an AIMessage and clears `pending_approval`. `agent_note` is an internal ops
    annotation (audit trail, reason for escalation, etc.) - it's logged, never shown to the
    passenger, since an agent could write anything there, including internal detail."""
    pending = state.get("pending_approval")
    if not pending:
        return {}

    decision = interrupt(
        {
            "type": "rebooking_approval",
            "pnr": pending["pnr"],
            "flight_number": pending["flight_number"],
            "compensation_usd": pending["compensation_usd"],
            "reason": pending["reason"],
        }
    )

    approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)
    note = decision.get("agent_note") if isinstance(decision, dict) else None
    if note:
        logger.info("pnr=%s approved=%s agent_note=%s", pending["pnr"], approved, note)

    if approved:
        reply = (
            f"Your rebooking with a {pending['compensation_usd']} USD travel credit has been "
            "approved by a Zenith Air agent and is now confirmed."
        )
    else:
        reply = (
            "A Zenith Air agent was unable to approve this rebooking as proposed - please "
            "contact support for alternative options."
        )

    return {"pending_approval": None, "messages": [AIMessage(content=reply)]}
