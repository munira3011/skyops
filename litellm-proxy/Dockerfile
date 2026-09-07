# SkyOps LiteLLM gateway - the only service holding real provider keys (OPENAI_API_KEY/
# ANTHROPIC_API_KEY). Built from requirements.txt (kept in sync with pyproject.toml/uv.lock via
# `uv export --format requirements-txt --no-dev -o requirements.txt`, per CLAUDE.md).
FROM python:3.12-slim

WORKDIR /app

# litellm's own startup banner has crashed under non-UTF-8 consoles before (see
# docs/progress.md) - harmless but unconditional here too.
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.yaml .

EXPOSE 4000

CMD ["litellm", "--config", "config.yaml", "--port", "4000", "--host", "0.0.0.0"]
