# SkyOps — Multi-agent airline assistant

A LangGraph-based multi-agent system for a fictional Dubai-based airline ("Zenith
Air"), built to demonstrate agent orchestration, RAG, LLM guardrails, and an LLM
gateway layer — not a production airline system. Handles customer queries (flight
status, booking details, disruption/rebooking, baggage/loyalty/check-in policy) and
gives staff/admin a role-gated ops surface for approvals and eval visibility.

## What it demonstrates

- **Multi-agent orchestration** (LangGraph): a supervisor node classifies intent (LLM
  first, deterministic keyword fallback if the gateway is down) and routes to one of
  four specialists, each of which can call the gateway LLM to answer the passenger's
  actual question from data it already looked up deterministically — never letting
  the LLM invent or fetch data itself.
- **RAG**: policy questions (baggage, loyalty tiers, check-in, rebooking) are answered
  from 4 markdown policy docs, chunked and embedded locally (Chroma, no cloud vector
  DB), with the LLM picking the right retrieved chunk rather than trusting a raw
  top-1 embedding match.
- **Guardrails**: an input guard blocks prompt injection/abuse/PII requests before
  they reach any specialist; an output guard checks the draft reply before it's ever
  shown to the customer. Both wrap the whole graph, so every specialist gets this
  protection "for free."
- **LLM gateway**: every model call goes through a LiteLLM proxy (primary + a
  cross-provider fallback model), never a provider SDK directly — the proxy is the
  only service holding real OpenAI/Anthropic keys.
- **Human-in-the-loop**: disruption compensation above a policy threshold pauses the
  graph (LangGraph `interrupt()`) for a staff sign-off before it's finalized.
- **Evals**: router accuracy, guardrail accuracy, and RAG groundedness/quality (ragas)
  test suites, plus a small dashboard showing the latest RAG eval run.

## Architecture

Three independent services, each its own deployable unit:

```
frontend (Streamlit)  ──HTTP──>  backend (FastAPI + LangGraph)  ──HTTP──>  litellm-proxy (LiteLLM)
                                        │                                         │
                                  Chroma (RAG) + SQLite                   OpenAI / Anthropic
                                  (staff auth, approval queue)
```

Full topology, state/reducer rules, and the HTTP API surface are documented in
[`docs/architecture.md`](docs/architecture.md). Build history and every bug found
along the way are in [`docs/progress.md`](docs/progress.md).

## Repo structure

```
backend/        FastAPI + LangGraph agent graph (Python 3.12, own uv project)
frontend/       Streamlit UI (Python 3.12, own uv project)
litellm-proxy/  LiteLLM gateway config (Python 3.12, own uv project)
evals/          Router accuracy, guardrail, and RAG quality (ragas) eval suites
infra/bicep/    Azure Container Apps infrastructure-as-code
.github/        CI/CD workflow (build + deploy to Azure)
docs/           Architecture and build-progress documentation
```

Each of `backend/`, `frontend/`, `litellm-proxy/` is a fully independent `uv` project
with its own `pyproject.toml`/`uv.lock`/`.venv` — not a shared environment. See each
one's own README for setup/run instructions.

## Quick start (local)

```
docker compose up --build
```

Requires `backend/.env`, `frontend/.env`, `litellm-proxy/.env` to exist first — copy
each from its `.env.example` and fill in real values (see each service's README).
Then:
- Frontend: http://localhost:8501
- Backend API docs: http://localhost:8000/docs
- litellm-proxy: internal to the compose network only (not published)

## Deployment

Deploys to Azure Container Apps (one Container Apps Environment, three apps) via
`infra/bicep/` and `.github/workflows/deploy.yml`, triggered on push to `main` or
manual dispatch. See `docs/architecture.md`'s "Deployment shape" section for the
infra design.

## Tech stack

LangGraph + LangChain · Chroma · FastAPI · Streamlit · LiteLLM · uv · Docker · Azure
Container Apps · Bicep · GitHub Actions
