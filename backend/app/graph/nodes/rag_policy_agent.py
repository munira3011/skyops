from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.gateway.client import GatewayError, chat_structured
from app.graph.state import AgentState
from app.rag.retriever import PolicyChunk, retrieve

# Retrieve more than the single top embedding match: semantic search sometimes ranks a
# lexically-similar but wrong section first, and the correct chunk can rank as low as #7
# (e.g. "what documents do I need for check-in" matching "Check-in windows" over "Travel
# document requirements" - found by the Day 7 ragas eval, docs/progress.md). The policy
# corpus is tiny (21 chunks total), so retrieving 8 candidates and letting the LLM pick the
# right one is cheap and reliably covers both failures found so far.
_RETRIEVE_K = 8

_SYNTHESIS_PROMPT = (
    "You are Zenith Air's policy assistant. Below are up to {k} candidate policy excerpts "
    "retrieved for the passenger's question - they came from semantic search, which "
    "sometimes ranks a lexically-similar but wrong excerpt first, so read all of them before "
    "choosing. Pick the ONE excerpt that actually answers the question, then answer using "
    "ONLY that excerpt - do not add information that isn't in it. If none of them fully "
    "answer the question, pick the closest one, say what it does cover, and suggest "
    "contacting support for the rest. Report which excerpt you used.\n\n{excerpts}"
)


class _PolicyAnswer(BaseModel):
    """Structured reply for `_synthesize` - the model must report which candidate excerpt it
    actually used, so the citation stays accurate now that more than one excerpt is passed in."""

    excerpt_index: int
    answer: str


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _citation(chunk: PolicyChunk) -> str:
    return f'(Source: {chunk.source}, section "{chunk.heading}")'


def _format_excerpts(chunks: list[PolicyChunk]) -> str:
    return "\n\n".join(
        f'Excerpt {i} (source: {chunk.source}, section "{chunk.heading}"):\n{chunk.text}'
        for i, chunk in enumerate(chunks)
    )


def _synthesize(query: str, chunks: list[PolicyChunk]) -> tuple[str, PolicyChunk]:
    """Returns (reply text with citation, the chunk actually used) - callers that only need the
    reply (rag_policy_agent) can discard the second value; evals/run_ragas_eval.py needs it to
    score the answer against the chunk that actually produced it, not just the top embedding
    match."""
    prompt = SystemMessage(content=_SYNTHESIS_PROMPT.format(k=len(chunks), excerpts=_format_excerpts(chunks)))
    result = chat_structured([prompt, HumanMessage(content=query)], _PolicyAnswer)
    index = result.excerpt_index if 0 <= result.excerpt_index < len(chunks) else 0
    chunk = chunks[index]
    return f"{result.answer}\n\n{_citation(chunk)}", chunk


def rag_policy_agent(state: AgentState) -> dict:
    """Reads the latest user message, retrieves the top-k candidate policy chunks via Chroma,
    and asks the gateway LLM to pick the best-matching one and answer from it - falling back
    to the raw top-ranked chunk's text if the gateway is unavailable."""
    query = _latest_user_text(state)
    chunks = retrieve(query, k=_RETRIEVE_K)
    if not chunks:
        reply = "I couldn't find a policy that answers that - could you rephrase your question?"
        return {"messages": [AIMessage(content=reply)]}

    try:
        reply, _ = _synthesize(query, chunks)
    except GatewayError:
        reply = f"{chunks[0].text}\n\n{_citation(chunks[0])}"

    return {"messages": [AIMessage(content=reply)]}
