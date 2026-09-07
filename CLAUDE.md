# SkyOps — multi-agent airline assistant

A LangGraph-based multi-agent system for a fictional Dubai-based airline ("Zenith
Air"), handling customer queries (booking, flight status, baggage/loyalty policy,
disruption rebooking) and internal ops queries. Built to demonstrate agent
orchestration, RAG, LLM guardrails, and a gateway layer — not a production airline
system.

## Project structure
Three independent uv projects, each with their own pyproject.toml/uv.lock/.venv —
NOT a single shared environment. This maps directly to three separate Docker
images/Azure Container Apps later.

- `backend/` — FastAPI + LangGraph agent graph (Python 3.12)
- `frontend/` — Streamlit UI (Python 3.12)
- `litellm-proxy/` — LiteLLM gateway config, its own container (Python 3.12)

## Package management — uv only
- Never `pip install` directly. Always `uv add <package>` (or `uv add --dev` for
  dev-only deps) so pyproject.toml and uv.lock stay accurate.
- After adding/removing a dependency, re-export: run
  `uv export --format requirements-txt --no-dev -o requirements.txt` inside that
  project's folder — Docker builds read this exported file.
- Run things with `uv run ...`, not by activating the venv manually.

## Stack
- LangGraph + LangChain for the agent graph
- Chroma (local, in-process, at backend/chroma_db/) for RAG — no cloud vector DB,
  deliberate choice to avoid Azure AI Search setup time
- FastAPI backend, Streamlit frontend
- LiteLLM proxy as its own service for the LLM gateway (primary + fallback model)
- Mocked data in backend/app/data/ (flights.json, pnr.json) and
  backend/app/data/policies/ (4 markdown docs: baggage, loyalty, rebooking,
  check-in) — all fictional, written from scratch to avoid any copyright issue
  with real airline policy text

## State schema (backend/app/graph/state.py)
- `messages`: Annotated[list, add_messages] — reducer appends, never overwrite
- Any dict-shaped field written by more than one node (e.g. `passenger`) MUST use
  a custom merge reducer (`{**left, **right}`), not plain overwrite — plain
  overwrite silently drops earlier nodes' fields. Check this every time a new
  field is added.
- `guardrail_flags` and similar accumulating lists use `operator.add` as the
  reducer so multiple nodes' flags append rather than clobber.
- Don't add a state field speculatively — add it when a node actually needs to
  read something an earlier node wrote. Re-audit reducers once all nodes exist.

## Graph pattern
Supervisor pattern: a `supervisor` node reads intent and sets `next`, conditional
edges route to the matching specialist agent, agent returns to supervisor
(or to a human-approval interrupt node for disruption/rebooking decisions above
a threshold — see rebooking_policy.md's human-approval rule), supervisor routes
to END when resolved.

## Conventions
- One file per node in `backend/app/graph/nodes/`
- Every node: `def node_name(state: AgentState) -> dict` — return only changed
  keys, never the full state
- Every node gets a one-line docstring: what it reads from state, what it writes
- Test every node standalone (call it directly with a fake state dict) before
  wiring it into the graph
- Tool/LLM calls go through the LiteLLM proxy client, never call a model
  provider directly
- Never hardcode secrets — read from environment variables, document required
  vars in .env.example
- Keep functions under ~40 lines; extract helpers rather than nesting logic

## Testing/evals
- evals/datasets/golden_qa.jsonl — router accuracy test set
- evals/test_agent_accuracy.py, evals/run_ragas_eval.py — run before considering
  a day's work "done" once evals exist (from Day 7 onward during initial build,
  ongoing after)

## Current phase
Day 6 of a 7-day build is done (graph/RAG/guardrails, FastAPI backend, Streamlit
frontend incl. staff approval UI, Docker/Bicep/CI infra). The whole project was
committed and pushed to `origin/main` for the first time on 2026-09-07, after a
full secret-scan of the git history (see docs/progress.md) — a prior
`docker compose config` incident had exposed real provider/app keys, all of
which have since been rotated and reverified. `docker compose up` has been
verified working end-to-end; the Bicep templates and GitHub Actions workflow
have not yet been verified against a real Azure subscription. Next up: that
Azure verification, then Day 7 — build out `evals/` (golden_qa.jsonl,
adversarial_prompts.jsonl, test_agent_accuracy.py, test_guardrails.py,
run_ragas_eval.py). Check docs/progress.md for exactly what's done and what's
next — read that file at the start of every session before making changes.