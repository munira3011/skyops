from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.nodes.booking_agent import booking_agent
from app.graph.nodes.booking_disruption_agent import booking_disruption_agent
from app.graph.nodes.flight_status_agent import flight_status_agent
from app.graph.nodes.human_approval import human_approval
from app.graph.nodes.rag_policy_agent import rag_policy_agent
from app.graph.state import AgentState
from app.graph.supervisor import supervisor
from app.guardrails.input_guard import input_guard
from app.guardrails.output_guard import output_guard

_SPECIALISTS = {
    "flight_status_agent": flight_status_agent,
    "rag_policy_agent": rag_policy_agent,
    "booking_agent": booking_agent,
    "booking_disruption_agent": booking_disruption_agent,
}


def _route_from_input_guard(state: AgentState) -> str:
    messages = state["messages"]
    return END if messages and isinstance(messages[-1], AIMessage) else "supervisor"


def _route_from_supervisor(state: AgentState) -> str:
    return state.get("next") or "output_guard"


def _route_from_disruption(state: AgentState) -> str:
    return "human_approval" if state.get("pending_approval") else "supervisor"


def build_graph(checkpointer=None) -> CompiledStateGraph:
    """`checkpointer` must be set to exercise `human_approval`'s interrupt/resume outside
    LangGraph Studio - Studio/the platform injects its own, so the module-level `graph` below
    (used by langgraph.json) is compiled without one."""
    builder = StateGraph(AgentState)
    builder.add_node("input_guard", input_guard)
    builder.add_node("supervisor", supervisor)
    builder.add_node("output_guard", output_guard)
    builder.add_node("human_approval", human_approval)
    for name, node in _SPECIALISTS.items():
        builder.add_node(name, node)

    for name in _SPECIALISTS:
        if name != "booking_disruption_agent":
            builder.add_edge(name, "supervisor")
    builder.add_conditional_edges(
        "booking_disruption_agent",
        _route_from_disruption,
        {"human_approval": "human_approval", "supervisor": "supervisor"},
    )
    builder.add_edge("human_approval", "supervisor")

    builder.add_edge(START, "input_guard")
    builder.add_conditional_edges(
        "input_guard", _route_from_input_guard, {"supervisor": "supervisor", END: END}
    )
    builder.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {**{name: name for name in _SPECIALISTS}, "output_guard": "output_guard"},
    )
    builder.add_edge("output_guard", END)
    return builder.compile(checkpointer=checkpointer)


graph = build_graph()


if __name__ == "__main__":
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    # A checkpointer is required for human_approval's interrupt()/resume to work; the
    # module-level `graph` above has none since Studio/the platform provides its own.
    dev_graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "dev-session"}}

    print("SkyOps dev chat - type a message, Ctrl+C to exit.")
    while True:
        try:
            text = input("you> ")
        except (EOFError, KeyboardInterrupt):
            break
        result = dev_graph.invoke({"messages": [HumanMessage(content=text)]}, config=config)
        while result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            print(f"bot> [needs Zenith Air agent approval] {payload}")
            answer = input("approve? (y/n)> ").strip().lower()
            result = dev_graph.invoke(Command(resume={"approved": answer == "y"}), config=config)
        print("bot>", result["messages"][-1].content)
