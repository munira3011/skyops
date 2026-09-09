# SkyOps litellm-proxy

LiteLLM gateway — the **only** service holding real provider keys
(`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`). `backend/` calls it via
`backend/app/gateway/client.py` and never a provider SDK directly. See the
[root README](../README.md) for the overall project.

## Setup

```
uv sync
cp .env.example .env
```

Fill in `.env`:
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — real provider keys.
- `LITELLM_MASTER_KEY` — a secret you invent yourself (not provider-issued); clients
  authenticate to this proxy with it. Must exactly match `LITELLM_API_KEY` in
  `../backend/.env`.

## Running

```
uv run litellm --config config.yaml --port 4000
```
Health check: `curl http://localhost:4000/health/readiness`.

On Windows, startup banners can crash under the default console encoding — run with
`PYTHONUTF8=1` set (or `setx PYTHONUTF8 1` once, persistently).

## Config

`config.yaml` defines two models, cross-provider so a single provider outage doesn't
take the assistant down:
- `primary` — `openai/gpt-4o-mini`
- `fallback` — `anthropic/claude-haiku-4-5-20251001`, used automatically if `primary`
  fails

`backend/`'s gateway client defaults to `primary` (set via `LITELLM_MODEL`); LiteLLM's
own router handles the fallback.
