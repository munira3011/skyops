"""Guardrail accuracy eval: calls `input_guard`/`output_guard` directly (standalone, per
CLAUDE.md's node-testing convention) against `datasets/adversarial_prompts.jsonl` - real
jailbreak/abuse/PII attempts that should be flagged, plus the two documented false-positive
regressions (Day 3's citation leak, Day 4's own-PNR-sharing) that must NOT be flagged.
"""
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.guardrails.input_guard import input_guard
from app.guardrails.output_guard import output_guard

_DATASET = Path(__file__).parent / "datasets" / "adversarial_prompts.jsonl"


def _load_cases() -> list[dict]:
    with open(_DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_guardrail_verdict(case):
    if case["guard"] == "input":
        state = {"messages": [HumanMessage(content=case["text"])]}
        result = input_guard(state)
        prefix = "input:"
    else:
        state = {"messages": [HumanMessage(content="(prior turn)"), AIMessage(content=case["text"])]}
        result = output_guard(state)
        prefix = "output:"

    flags = result.get("guardrail_flags", [])
    if case["expected_flagged"]:
        assert flags, f"{case['id']}: expected a {case['expected_category']} flag but none was raised"
        assert any(flag.startswith(f"{prefix}{case['expected_category']}") for flag in flags), (
            f"{case['id']}: expected category {case['expected_category']!r}, got {flags}"
        )
    else:
        assert not flags, f"{case['id']}: expected no flag, got {flags}"
