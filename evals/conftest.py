"""Shared eval setup: makes `app.*` importable (evals isn't its own uv project - it runs
under backend's venv/dependencies, since it needs to invoke the real graph/gateway/
guardrails), loads backend/.env for convenience, and skips the whole session with a clear
message if no live litellm-proxy is configured - these evals measure real LLM behavior
(routing/guardrail accuracy, RAG groundedness), not the keyword-fallback paths, so a mocked
gateway would defeat the point.
"""
import os
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass


@pytest.fixture(scope="session", autouse=True)
def _require_live_gateway():
    if not (os.environ.get("LITELLM_BASE_URL") and os.environ.get("LITELLM_API_KEY")):
        pytest.skip(
            "LITELLM_BASE_URL/LITELLM_API_KEY not set - these evals need a live litellm-proxy "
            "(see backend/.env.example), not the keyword-fallback path."
        )
