from typing import Optional

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from app import db
from app.graph.runtime import api_graph

router = APIRouter()


class ApprovalStateResponse(BaseModel):
    thread_id: str
    pending_approval: Optional[dict] = None
    last_reply: Optional[str] = None


class ApprovalDecision(BaseModel):
    approved: bool
    agent_note: Optional[str] = None


class ApprovalDecisionResponse(BaseModel):
    thread_id: str
    reply: str
    pending_approval: Optional[dict] = None


class PendingApprovalSummary(BaseModel):
    thread_id: str
    pnr: str
    flight_number: str
    compensation_usd: int
    reason: str
    created_at: str


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


@router.get("/approvals", response_model=list[PendingApprovalSummary])
def list_pending_approvals() -> list[PendingApprovalSummary]:
    """Lists every thread currently paused at `human_approval`'s interrupt - backed by a small
    SQLite table (see app/db.py) that routes/chat.py writes to whenever a turn sets
    `pending_approval`, since the graph's checkpointer itself has no "list all threads" API."""
    return [PendingApprovalSummary(**row) for row in db.list_pending_approvals()]


@router.get("/approvals/{thread_id}", response_model=ApprovalStateResponse)
def get_approval_state(thread_id: str) -> ApprovalStateResponse:
    """Reads a thread's current state - `pending_approval` is set only while that thread is
    paused at `human_approval`'s interrupt, waiting for a decision."""
    state = api_graph.get_state(_config(thread_id))
    messages = state.values.get("messages", [])
    return ApprovalStateResponse(
        thread_id=thread_id,
        pending_approval=state.values.get("pending_approval"),
        last_reply=messages[-1].content if messages else None,
    )


@router.post("/approvals/{thread_id}/decision", response_model=ApprovalDecisionResponse)
def decide_approval(thread_id: str, decision: ApprovalDecision) -> ApprovalDecisionResponse:
    """Resumes `thread_id` past `human_approval`'s interrupt with a Zenith Air agent's decision."""
    state = api_graph.get_state(_config(thread_id))
    if not state.values.get("pending_approval"):
        raise HTTPException(status_code=404, detail="No pending approval for this thread.")

    resume_value = {"approved": decision.approved, "agent_note": decision.agent_note}
    result = api_graph.invoke(Command(resume=resume_value), config=_config(thread_id))
    db.resolve_pending_approval(thread_id)
    return ApprovalDecisionResponse(
        thread_id=thread_id,
        reply=result["messages"][-1].content,
        pending_approval=result.get("pending_approval"),
    )
