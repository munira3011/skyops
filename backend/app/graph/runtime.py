from langgraph.checkpoint.memory import InMemorySaver

from app.graph.graph import build_graph

# One shared, checkpointed graph instance for the HTTP API - routes/chat.py invokes turns
# against it, routes/ops.py resumes interrupted threads against the same instance/checkpointer
# so a thread started via chat.py can be approved via ops.py. Deliberately in-memory (resets on
# restart) rather than a persistent checkpointer, matching this project's no-external-DB choice
# for Chroma - fine for the fictional-airline demo scope, revisit if this needs to survive
# restarts.
api_graph = build_graph(checkpointer=InMemorySaver())
