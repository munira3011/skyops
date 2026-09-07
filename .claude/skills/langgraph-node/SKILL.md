---
name: langgraph-node
description: Use when adding or modifying a LangGraph node in backend/app/graph/ (a specialist agent, a guardrail, or any other node) - covers this repo's node signature, gateway-first/keyword-fallback pattern, state reducer rules, wiring into graph.py/supervisor.py, and the required standalone test before wiring in.
---

# Writing a SkyOps graph node

This skill captures the convention every existing node in
`backend/app/graph/nodes/` and `backend/app/guardrails/` follows. Read
`docs/architecture.md` first for how the graph fits together; this skill is
about how to write one more piece of it consistently.

## 1. Signature and shape

```python
def my_node(state: AgentState) -> dict:
    """One line: what it reads from state, what it writes."""
    ...
```

- Exactly this signature. Return **only the keys that changed** — never the
  full state. LangGraph merges your return dict into state via each field's
  reducer (see step 4); returning unchanged keys risks accidentally
  re-triggering a reducer (e.g. re-appending a message) for no reason.
- One-line docstring, always in the "reads X, writes Y" shape — see
  `flight_status_agent.py`, `booking_disruption_agent.py`,
  `human_approval.py` for examples. Skip prose about *why* the node exists;
  that belongs in `docs/progress.md`, not the code.
- Keep the function under ~40 lines. Extract helpers (`_find_booking`,
  `_assess`, `_format_reply`-style private functions above the node) rather
  than nesting logic inline — every existing node does this.
- One file per node in `backend/app/graph/nodes/` (guardrails live in
  `backend/app/guardrails/` instead, same shape).

## 2. Reading input

Two common patterns, both already established:

- **From state first, message second**: if the node needs a `booking`/
  `flight`, check `state.get("booking")`/`state.get("flight")` before
  falling back to parsing the latest `HumanMessage` (a PNR regex
  `\b[A-Z0-9]{5,7}\b`, or a flight-number regex `\b[A-Z]{2}\d{3,4}\b`). See
  `_find_booking` in `booking_disruption_agent.py` / `booking_agent.py`, or
  `_find_flight_number` in `flight_status_agent.py`. This lets a later node
  in the same turn reuse an earlier node's lookup instead of re-parsing.
- **Fixture data**: mocked JSON (`flights.json`, `pnr.json`) is loaded via
  an `lru_cache(maxsize=1)`-wrapped module function (e.g.
  `_flights_by_number()`), not re-read from disk per call.

## 3. LLM calls: gateway-first, keyword fallback, always

Every node/guardrail that calls an LLM follows the same shape — **never
skip the fallback**, since it's what keeps the assistant answering when
`litellm-proxy` is down:

```python
from app.gateway.client import GatewayError, chat, chat_structured

def _classify(text: str) -> tuple[SomeType, str]:
    try:
        result = chat_structured([...], SomeSchema)  # or chat([...]) for freeform prose
        return result.some_field, "llm classified as ..."
    except GatewayError as exc:
        result = _keyword_fallback(text)
        return result, f"gateway unavailable ({exc}), keyword fallback matched {result}"
```

- Use `chat_structured(messages, schema)` — not `chat()` + freeform-text
  parsing — whenever the result gets written to state (a route, a verdict,
  anything besides prose shown to the user). Define the schema as a small
  `pydantic.BaseModel` (see `supervisor._RouteDecision`,
  `guardrails/schema.py`'s `GuardrailVerdict`). Use plain `chat()` only for
  freeform prose that's the reply itself (e.g.
  `rag_policy_agent._synthesize`).
- Never construct a model client directly (`ChatOpenAI(...)`, or any
  provider SDK) — always go through `app/gateway/client.py`. It's the only
  file that points at `litellm-proxy`.
- The keyword fallback should be a small, explicit deterministic function
  (regex/substring checks), not a second LLM call. It only has to be
  "reasonable," not as smart as the LLM path — it exists for degraded-mode
  availability, not primary accuracy.
- Record which path was taken in a reason/handoff string if the node
  writes one (see `handoff_reason` in `supervisor.py`) — this made a real
  Studio-routing bug diagnosable in Day 3 (see `docs/progress.md`).

## 4. Touching `AgentState` (`backend/app/graph/state.py`)

Before adding a new field:

- **Don't add it speculatively.** Add it only when a node actually needs
  to read something an earlier node wrote (CLAUDE.md rule).
- **Decide the reducer up front.** If more than one node can write this
  field in the same run, plain overwrite silently drops whichever node ran
  first — you need `Annotated[T, operator.add]` (for a list, like
  `guardrail_flags`) or a custom merge reducer (`{**left, **right}` for a
  dict-shaped field multiple nodes patch different keys of). If only one
  node ever writes it, plain overwrite is fine and simplest — most of
  `AgentState`'s fields are this case; don't add a reducer you don't need.
- **Re-audit the whole table** in `docs/architecture.md`'s state section
  after adding a field — that table exists precisely so this check doesn't
  get skipped.

## 5. Wiring into `graph.py` / `supervisor.py`

1. Add the node: `builder.add_node("my_node", my_node)`.
2. Add its outbound edge. Most specialists go straight back to
   `supervisor` (`builder.add_edge("my_node", "supervisor")`). If your
   node can conditionally route elsewhere (like
   `booking_disruption_agent` -> `human_approval`), add a conditional edge
   function instead (see `_route_from_disruption`) — keep the branching
   function itself trivial (one `state.get(...)` check), not business
   logic.
3. If it's a new specialist supervisor should route to: add it to
   `_SPECIALISTS` in `graph.py`, add a branch to `_CLASSIFIER_PROMPT` in
   `supervisor.py` (worded to clearly discriminate it from the existing
   routes — see how `booking_agent` vs. `booking_disruption_agent` are
   distinguished in the prompt, since ambiguous wording is a real source
   of misrouting), and add a keyword list for `_keyword_classify`'s
   fallback path. Order keyword checks so a more-specific list is checked
   before a more-generic one that could shadow it (a real bug: `"my pnr"`
   in `_BOOKING_KEYWORDS` used to shadow disruption requests — see
   `docs/progress.md`'s Day-5 postmortem).
4. If it's a guardrail rather than a specialist, wire it as its own graph
   stage (`input_guard` before `supervisor`, `output_guard` after) with a
   conditional edge that can short-circuit to `END`, not as a
   `_SPECIALISTS` entry.

## 6. Test standalone before wiring in

Per CLAUDE.md: call the node directly with a fake state dict first.

```python
from app.graph.nodes.my_node import my_node
result = my_node({"messages": [HumanMessage(content="...")], "booking": None, ...})
print(result)
```

Cover: the happy path, the "couldn't find X" path, and — if the node calls
the gateway — both the LLM path and the `GatewayError` fallback path (you
can force the fallback by pointing `LITELLM_BASE_URL` at a closed port, or
by temporarily raising `GatewayError` from a monkeypatched `chat`/
`chat_structured`). Only wire it into `graph.py` and re-test the full graph
run once the node works alone.
