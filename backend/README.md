# SkyOps backend

FastAPI + LangGraph agent graph. See the [root README](../README.md) for the overall
project, and [`docs/architecture.md`](../docs/architecture.md) for the graph topology,
state/reducer rules, and full HTTP API reference.

## Setup

```
uv sync
cp .env.example .env
```

Fill in `.env`:
- `LITELLM_BASE_URL`/`LITELLM_API_KEY` — point at a running `litellm-proxy` (see
  `../litellm-proxy/README.md`). `LITELLM_API_KEY` must match that service's
  `LITELLM_MASTER_KEY` exactly.
- `SKYOPS_API_KEY` — required on every route except `/health`/docs. The frontend
  authenticates with this too (must match `../frontend/.env`'s `SKYOPS_API_KEY`).
- `SKYOPS_OPS_API_KEY` — additionally required on `/ops/*` (service-to-service; staff
  can instead log in via `/ops/auth/login` — see below).

`.env` is not auto-loaded by the app itself (`app/main.py` reads straight from
`os.environ`) — export it into your shell before running anything below, e.g. from
`backend/`:
```
set -a && source <(tr -d '\r' < .env) && set +a   # bash/git-bash
```

## Running

```
uv run uvicorn app.main:app --port 8000
```
API docs at `http://localhost:8000/docs`.

Dev REPL (no HTTP, drives the graph directly - useful for testing the
interrupt/resume approval flow):
```
uv run python -m app.graph.graph
```

Ingest the RAG policy docs into Chroma (needed once, or after changing
`app/data/policies/*.md` - `backend/chroma_db/` is gitignored):
```
uv run python -m app.rag.ingest
```

Create a staff account (role `staff` by default, or pass `admin` for full access
including the eval dashboard):
```
uv run python -m app.db <username> <password> [staff|admin]
```

## Testing / evals

```
uv run pytest ../evals -v          # router accuracy + guardrail suites
uv run python ../evals/run_ragas_eval.py   # RAG quality (ragas) - writes
                                             # app/data/eval_results/rag_eval.json,
                                             # served by GET /ops/evals/rag
```
Both need a live `litellm-proxy` — they measure real LLM behavior, not the
keyword-fallback paths. Per `CLAUDE.md`, run these before considering a change done.

## Structure

```
app/graph/          StateGraph, supervisor, specialist nodes (graph/nodes/)
app/guardrails/      input_guard / output_guard
app/rag/              Chroma ingestion + retrieval
app/gateway/          the one place a model client is constructed (LiteLLM proxy)
app/routes/           chat.py (customer), ops.py / ops_auth.py (staff/admin), evals.py
app/middleware/        auth, rate limiting, logging, error handling
app/data/              mocked flights.json/pnr.json + policy markdown docs
```

See [`.claude/skills/langgraph-node/SKILL.md`](../.claude/skills/langgraph-node/SKILL.md)
for the convention every node follows.
