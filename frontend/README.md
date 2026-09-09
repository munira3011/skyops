# SkyOps frontend

Streamlit UI — a thin client over the backend's HTTP API only (never calls the LLM
gateway or LangGraph directly). See the [root README](../README.md) for the overall
project.

## Setup

```
uv sync
cp .env.example .env
```

Fill in `.env`:
- `SKYOPS_API_BASE_URL` — the backend's URL (default `http://localhost:8000`).
- `SKYOPS_API_KEY` — must exactly match `SKYOPS_API_KEY` in `../backend/.env`.

## Running

```
uv run streamlit run app.py
```
Opens at `http://localhost:8501`.

## Pages

Sidebar navigation, role-gated (server-side enforced too, not just hidden in the UI —
see `../backend/app/middleware/auth.py`):

| Auth state | Pages |
|---|---|
| Logged out (default) | **Customer Chat** + **Staff Login** |
| Logged in, `staff` role | **Staff Approval** only |
| Logged in, `admin` role | **Customer Chat**, **Staff Approval**, **Eval Dashboard** |

- **Customer Chat** — streamed replies, multi-turn via a backend-checkpointed thread,
  syncs from the backend's authoritative history (so an out-of-band staff decision
  becomes visible on refresh).
- **Staff Login** — username/password against the backend's staff account table (see
  `../backend/README.md` for creating an account).
- **Staff Approval** — the pending disruption-compensation approval queue; approve/
  reject with an internal-only note.
- **Eval Dashboard** (admin only) — the last saved `evals/run_ragas_eval.py` run:
  faithfulness/context-precision scores and a pass/fail badge. A snapshot, not a live
  re-run.
