# SkyOps frontend - Streamlit UI, thin client over the backend's HTTP API only. Built from
# requirements.txt (kept in sync with pyproject.toml/uv.lock via `uv export --format
# requirements-txt --no-dev -o requirements.txt`, per CLAUDE.md).
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.headless", "true", "--server.address", "0.0.0.0", "--server.port", "8501"]
