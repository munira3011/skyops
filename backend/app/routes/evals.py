import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "eval_results" / "rag_eval.json"


@router.get("/rag")
def get_rag_eval_results() -> dict:
    """Reads the latest evals/run_ragas_eval.py run's persisted results (gitignored generated
    artifact, see that script) - evals run out-of-band, needing ragas/pytest (dev-only deps) and
    a live litellm-proxy, so this just serves the last saved snapshot rather than re-running
    anything on request."""
    if not _RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No RAG eval results yet - run `uv run python ../evals/run_ragas_eval.py` from backend/.",
        )
    return json.loads(_RESULTS_PATH.read_text())
