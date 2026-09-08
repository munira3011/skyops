"""Router accuracy eval: runs supervisor's live LLM classification (via the real graph) against
`datasets/golden_qa.jsonl` and checks each message lands on the expected specialist.

Uses the module-level `graph` (no checkpointer - fine here since none of these golden
messages exercise booking_disruption_agent's approval interrupt with the real fixture data,
per docs/progress.md). Each case is a fresh, independent `graph.invoke` call.
"""
import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from app.graph.graph import graph

_DATASET = Path(__file__).parent / "datasets" / "golden_qa.jsonl"


def _load_cases() -> list[dict]:
    with open(_DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_router_accuracy(case):
    result = graph.invoke({"messages": [HumanMessage(content=case["message"])]})
    expected = None if case["expected_route"] == "none" else case["expected_route"]
    actual = result.get("active_agent")

    assert actual == expected, (
        f"{case['id']} {case['message']!r}: expected route {expected!r}, got {actual!r} "
        f"(handoff_reason={result.get('handoff_reason')!r})"
    )
    assert result["messages"][-1].content, f"{case['id']}: got an empty reply"
