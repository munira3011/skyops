# Architecture

SkyOps is a LangGraph-based multi-agent assistant for a fictional airline
("Zenith Air"). This doc describes how the pieces fit together as of Day 6.
For what's built vs. not yet, see `docs/progress.md`; for conventions, see
`CLAUDE.md`.

## Services

Three independent processes, each its own uv project (`backend/`,
`frontend/`, `litellm-proxy/`) and, from Day 6, its own Docker image:

```
┌─────────────┐      HTTP (X-API-Key)      ┌─────────────┐      HTTP (OpenAI-      ┌───────────────┐
│  frontend    │ ─────────────────────────> │   backend    │ ──── compatible) ────> │ litellm-proxy  │
│  (Streamlit) │ <───────────────────────── │  (FastAPI +  │ <───────────────────── │  (LiteLLM)     │
│  :8501       │        SSE / JSON          │  LangGraph)  │                        │  :4000         │
└─────────────┘                             │  :8000       │                        └───────┬───────┘
                                             └──────┬──────┘                                 │
                                                    │                                          ├─ openai/gpt-4o-mini (primary)
                                             ┌──────┴──────┐                                  └─ anthropic/claude-haiku-4-5 (fallback)
                                             │ chroma_db/   │  (local, in-process RAG store)
                                             │ staff.db     │  (SQLite - staff auth + approval queue)
                                             └─────────────┘
```

- **frontend** never calls the gateway or the graph directly — only the
  backend's HTTP API (`POST /chat/message`, `POST /chat/stream`,
  `GET /chat/history/{thread_id}`, `POST /ops/auth/*`, `GET/POST /ops/*`).
  This keeps the frontend a thin client over one HTTP contract, swappable
  for another UI later without touching backend logic.
- **backend** is the only thing that talks to `litellm-proxy`, and only
  through `app/gateway/client.py` — no node constructs a model client
  itself, and no code calls an LLM provider directly (per CLAUDE.md).
- **litellm-proxy** is the only thing holding real provider keys
  (`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`). The backend authenticates to it
  with `LITELLM_API_KEY` (mapped to `LITELLM_MASTER_KEY` in
  `litellm-proxy/config.yaml`) and never sees the upstream keys.

## The graph

`backend/app/graph/graph.py` builds a `StateGraph(AgentState)` with a
supervisor pattern: a router node picks a specialist, the specialist
returns to the router, the router ends the turn once the latest message is
an `AIMessage`.

```
START
  │
  v
input_guard ──(flagged)──> END  (refusal AIMessage, specialists never run)
  │ (clean)
  v
supervisor <──────────────────────────────────────────┐
  │  routes via `next`                                 │
  ├─> flight_status_agent ──────────────────────────────┤
  ├─> rag_policy_agent ──────────────────────────────────┤
  ├─> booking_agent ──────────────────────────────────────┤
  └─> booking_disruption_agent                            │
         │  (pending_approval set?)                        │
         ├─ yes ─> human_approval ─(interrupt/resume)──────┤
         └─ no ───────────────────────────────────────────>┘
  │ (no more routing - last message is AIMessage)
  v
output_guard ──(flagged)──> replaces reply with a safe message
  │
  v
END
```

- **Routing** (`supervisor.py`): LLM-first via `chat_structured` +
  `_RouteDecision` (a `Literal` over the 4 specialist names + `"none"`),
  constrained through tool-calling so the model can't return a
  non-conforming route. Falls back to deterministic keyword matching
  (`_keyword_classify`) if the gateway raises `GatewayError` — every LLM
  call site in the graph has this same fallback shape, so the assistant
  degrades to "still functional, less smart" rather than failing outright
  when `litellm-proxy` is down.
- **Specialists** (`graph/nodes/`): each reads what it needs from state
  (`booking`, `flight`, or a PNR/flight-number parsed from the latest
  message) and writes an `AIMessage` reply, and where relevant, `booking`/
  `flight`/`pending_approval`. See `.claude/skills/langgraph-node/SKILL.md`
  for the node-authoring convention.
- **`human_approval`**: the only interrupt point. `booking_disruption_agent`
  sets `pending_approval` when proposed compensation is above $300
  (`rebooking_policy.md`'s threshold); `graph.py`'s conditional edge
  (`_route_from_disruption`) sends the turn to `human_approval`, which
  calls LangGraph's `interrupt()` and pauses until resumed with
  `Command(resume={"approved": bool, "agent_note": Optional[str]})`.
  Requires a checkpointer — see "Two compiled instances" below.
- **Guardrails** (`guardrails/`): `input_guard` runs first, before
  `supervisor`, and can short-circuit straight to `END` with a refusal,
  so a flagged message never reaches a specialist or the LLM router.
  `output_guard` runs last, after `supervisor` has nothing left to route
  (checked via the same "last message is an AIMessage" idiom used to end
  supervisor's own loop), and can replace the draft reply before it's
  ever returned to the caller. Both use the shared `GuardrailVerdict`
  schema (`flagged`, `category`, `reason`) via `chat_structured`, with a
  deterministic keyword fallback on `GatewayError`, same pattern as
  routing.

## State (`graph/state.py`)

`AgentState` is a `TypedDict`. Fields written by exactly one node use plain
overwrite; anything else needs a reducer, or a later writer silently drops
an earlier node's fields:

| Field | Reducer | Why |
|---|---|---|
| `messages` | `add_messages` | every node appends, never replaces, the transcript |
| `guardrail_flags` | `operator.add` | `input_guard`/`output_guard` (and any future guardrail) each append flags independently |
| `booking`, `flight`, `active_agent`, `next`, `handoff_reason`, `pending_approval` | none (overwrite) | each is written by exactly one node per turn today |

Per CLAUDE.md: don't add a field speculatively, and re-check this table
whenever a new node writes a field more than one other node also writes.

## Two compiled graph instances

`build_graph(checkpointer=None)` is called twice, deliberately with
different checkpointing:

- **`graph.py`'s module-level `graph`** — no checkpointer. This is what
  `backend/langgraph.json` points LangGraph Studio/the platform at; the
  platform injects its own checkpointer, so passing one here would
  conflict.
- **`runtime.py`'s `api_graph`** — `InMemorySaver()`. One shared instance
  the FastAPI process invokes/resumes against. `routes/chat.py` and
  `routes/ops.py` must operate on the *same* instance, since an approval
  a customer triggers via chat has to be resumable from the ops
  endpoints against the same checkpointed thread. In-memory means a
  backend restart drops all in-flight threads/sessions — an accepted
  limitation at this scope (see `docs/progress.md`), matching the
  no-external-DB choice made for Chroma/rate-limiting elsewhere.

## HTTP API (`backend/app/routes/`, `backend/app/middleware/`)

- **Auth** (`middleware/auth.py`): every route except `/health`/docs
  requires `X-API-Key` (`SKYOPS_API_KEY`). `/ops/*` additionally requires
  either `X-Ops-API-Key` (`SKYOPS_OPS_API_KEY`, service-to-service) or a
  valid staff bearer token (role `"staff"`, from `POST /ops/auth/login`) —
  a leaked customer-chat key alone can't reach approvals.
- **Rate limiting** (`middleware/rate_limit.py`): fixed-window, in-memory,
  per client IP. Single-process/resets-on-restart by design; known gap if
  this ever runs behind a reverse proxy without `X-Forwarded-For` handling
  (see `docs/progress.md`).
- **Chat** (`routes/chat.py`): `POST /chat/message` (blocking),
  `POST /chat/stream` (SSE — a "status" event per completed graph node,
  then the already-guardrail-approved final reply streamed word-by-word;
  raw LLM tokens are never forwarded live, since `output_guard` needs the
  complete text before anything is shown to the customer), and
  `GET /chat/history/{thread_id}` (reads the authoritative checkpointed
  state, so a customer's UI can resync after an out-of-band change like a
  staff approval).
- **Ops** (`routes/ops.py`, `routes/ops_auth.py`): staff login/logout,
  `GET /ops/approvals` (pending-approval queue, backed by
  `backend/app/db.py`'s `pending_approvals` SQLite table — the checkpointer
  itself has no "list all interrupted threads" API), and
  `POST /ops/approvals/{thread_id}/decision` (resumes the graph, then
  deletes the queue row).

## Data & storage

All local, no external services (deliberate, to avoid cloud-setup time —
see CLAUDE.md):

- `backend/app/data/{flights.json,pnr.json}` — mocked flight/booking
  fixtures, fictional.
- `backend/app/data/policies/*.md` — 4 fictional policy docs, chunked by
  `##` heading and embedded into `backend/chroma_db/` (gitignored,
  regenerated via `rag/ingest.py`) for `rag_policy_agent`.
- `backend/staff.db` (gitignored SQLite) — staff accounts
  (PBKDF2-hashed passwords) and the pending-approvals queue.
- In-memory only, lost on restart: LangGraph checkpoints (`api_graph`),
  staff sessions (`app/sessions.py`), rate-limit counters.

## Deployment shape (Day 6)

Each service builds to its own Docker image (`backend/Dockerfile`,
`frontend/Dockerfile`, `litellm-proxy/Dockerfile`), runnable together via
`docker-compose.yml` for local multi-container testing, and deployed as
three separate Azure Container Apps in one Container Apps Environment via
`infra/bicep/` + `.github/workflows/deploy.yml`. This mirrors the
three-independent-projects structure all the way through — no shared base
image or monorepo build step, matching CLAUDE.md's "maps directly to three
separate Docker images/Azure Container Apps later."
