from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.gateway.client import GatewayError, chat
from app.graph.state import AgentState
from app.rag.retriever import PolicyChunk, retrieve

_SYNTHESIS_PROMPT = (
    "You are Zenith Air's policy assistant. Answer the passenger's question using "
    "ONLY the policy excerpt below - do not add information that isn't in it. If the "
    "excerpt doesn't fully answer the question, say what it does cover and suggest "
    "contacting support for the rest. Keep the answer to a few sentences.\n\n"
    'Policy excerpt (source: {source}, section "{heading}"):\n{text}'
)


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _citation(chunk: PolicyChunk) -> str:
    return f'(Source: {chunk.source}, section "{chunk.heading}")'


def _synthesize(query: str, chunk: PolicyChunk) -> str:
    prompt = SystemMessage(content=_SYNTHESIS_PROMPT.format(source=chunk.source, heading=chunk.heading, text=chunk.text))
    response = chat([prompt, HumanMessage(content=query)])
    return f"{response.content}\n\n{_citation(chunk)}"


def rag_policy_agent(state: AgentState) -> dict:
    """Reads the latest user message, retrieves the best-matching policy chunk via Chroma, and asks the gateway LLM to answer from it - falling back to the raw chunk text if the gateway is unavailable."""
    query = _latest_user_text(state)
    chunks = retrieve(query, k=1)
    if not chunks:
        reply = "I couldn't find a policy that answers that - could you rephrase your question?"
        return {"messages": [AIMessage(content=reply)]}

    chunk = chunks[0]
    try:
        reply = _synthesize(query, chunk)
    except GatewayError:
        reply = f"{chunk.text}\n\n{_citation(chunk)}"

    return {"messages": [AIMessage(content=reply)]}
