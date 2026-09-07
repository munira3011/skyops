# SkyOps backend - FastAPI + LangGraph. Built from requirements.txt (kept in sync with
# pyproject.toml/uv.lock via `uv export --format requirements-txt --no-dev -o requirements.txt`,
# per CLAUDE.md - re-run that export after any `uv add`/`uv remove` before rebuilding this image).
FROM python:3.12-slim

WORKDIR /app

# Windows/Git-Bash consoles aren't the concern here, but langgraph/litellm-style startup banners
# have crashed under non-UTF-8 encodings before (see docs/progress.md) - set this unconditionally.
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY langgraph.json .

# Chroma's collection is a plain get_collection() in retriever.py (not get_or_create) - it must
# exist before the app starts, or rag_policy_agent crashes on first query. Policy docs are static
# fixture data baked into this image, so ingest once at build time rather than needing a
# persistent volume or a runtime init step.
RUN python -m app.rag.ingest

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
