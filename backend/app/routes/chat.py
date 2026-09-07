import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app import db
from app.graph.runtime import api_graph

router = APIRouter()

# Friendly progress labels shown while /stream's graph run is in flight, keyed by node name -
# real execution progress (from stream_mode="updates"), not a fake/decorative animation.
_STATUS_BY_NODE = {
    "input_guard": "Checking your message...",
    "supervisor": "Figuring out how to help...",
    "flight_status_agent": "Looking up flight status...",
    "booking_agent": "Pulling up your booking...",
    "booking_disruption_agent": "Checking your rebooking options...",
    "rag_policy_agent": "Checking policy details...",
    "human_approval": "Processing the approval...",
    "output_guard": "Finalizing your answer...",
}
_WORD_REVEAL_DELAY_SECONDS = 0.02


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    pending_approval: Optional[dict] = None


class ChatHistoryMessage(BaseModel):
    role: str
    content: str


class ChatHistoryResponse(BaseModel):
    thread_id: str
    messages: list[ChatHistoryMessage]
    pending_approval: Optional[dict] = None


@router.get("/history/{thread_id}", response_model=ChatHistoryResponse)
def get_history(thread_id: str) -> ChatHistoryResponse:
    """Returns the authoritative message history for `thread_id`, straight from the graph's
    checkpointer - not whatever a client happens to have cached locally. A client must call this
    to see messages another actor wrote into the same thread out-of-band (e.g. human_approval's
    resume, triggered from routes/ops.py by a staff member, not by this client at all)."""
    state = api_graph.get_state({"configurable": {"thread_id": thread_id}}).values
    messages = state.get("messages", [])
    return ChatHistoryResponse(
        thread_id=thread_id,
        messages=[
            ChatHistoryMessage(role="user" if isinstance(m, HumanMessage) else "assistant", content=m.content)
            for m in messages
        ],
        pending_approval=state.get("pending_approval"),
    )


@router.post("/message", response_model=ChatResponse)
def send_message(body: ChatRequest) -> ChatResponse:
    """Runs one turn of the graph for `thread_id` (a new one is created if omitted). If the turn
    hits `human_approval`'s interrupt, the reply is still the pre-interrupt message written by
    booking_disruption_agent (e.g. "forwarded for review") - resolving the approval itself is a
    `routes/ops.py` concern, not this endpoint's."""
    thread_id = body.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    result = api_graph.invoke({"messages": [HumanMessage(content=body.message)]}, config=config)
    pending_approval = result.get("pending_approval")
    if pending_approval:
        db.record_pending_approval(thread_id, pending_approval)
    return ChatResponse(thread_id=thread_id, reply=result["messages"][-1].content, pending_approval=pending_approval)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream_turn(message: str, thread_id: str):
    """Streams real node-progress while the graph runs, then reveals the final reply
    word-by-word once it's ready. Deliberately does NOT stream raw LLM tokens as they're
    generated inside rag_policy_agent - output_guard must see and approve the COMPLETE reply
    before any of it reaches the client, so nothing here can bypass the guardrail pipeline;
    only the already-approved final text is ever sent."""
    config = {"configurable": {"thread_id": thread_id}}
    shown_labels: set[str] = set()

    for update in api_graph.stream(
        {"messages": [HumanMessage(content=message)]}, config=config, stream_mode="updates"
    ):
        for node_name in update:
            label = _STATUS_BY_NODE.get(node_name)
            # supervisor runs twice per turn (route, then end-of-turn) - only show its label
            # once, since the second pass isn't a new classification.
            if label and label not in shown_labels:
                yield _sse("status", {"text": label})
                shown_labels.add(label)

    state = api_graph.get_state(config).values
    messages = state.get("messages", [])
    reply_text = messages[-1].content if messages else ""
    pending_approval = state.get("pending_approval")
    if pending_approval:
        db.record_pending_approval(thread_id, pending_approval)

    words = reply_text.split(" ")
    for i, word in enumerate(words):
        yield _sse("token", {"text": word if i == 0 else f" {word}"})
        time.sleep(_WORD_REVEAL_DELAY_SECONDS)

    yield _sse("done", {"thread_id": thread_id, "reply": reply_text, "pending_approval": pending_approval})


@router.post("/stream")
def stream_message(body: ChatRequest) -> StreamingResponse:
    """Server-Sent Events version of /message - "status" events track real graph progress,
    then "token" events reveal the final (guardrail-approved) reply progressively, then one
    "done" event carries thread_id/pending_approval."""
    thread_id = body.thread_id or str(uuid.uuid4())
    return StreamingResponse(_stream_turn(body.message, thread_id), media_type="text/event-stream")
