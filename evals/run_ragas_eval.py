"""Day 7 RAG eval: measures rag_policy_agent's grounded-answer quality with ragas.

Runs every `rag_policy_agent`-routed question from `datasets/golden_qa.jsonl` through the
real retrieval + synthesis path (`app.rag.retriever.retrieve` + `rag_policy_agent`'s own
`_synthesize` helper, called standalone per CLAUDE.md's node-testing convention - not through
the full graph, since routing accuracy is already covered by test_agent_accuracy.py) and
scores each answer with:

- faithfulness: does the synthesized answer only claim things the retrieved policy chunk
  actually supports? (catches hallucination beyond the excerpt)
- context precision (reference-free): was the retrieved chunk actually relevant/useful for
  answering the question? (catches a bad retrieval that the LLM manages to route around)

Both metrics use the app's own gateway client (`get_chat_model()`) as the judge LLM, routed
through litellm-proxy like every other model call in this project - never a provider
directly, per CLAUDE.md.

`answer_relevancy` is deliberately not included: it requires an embeddings model, and this
project doesn't have an embeddings route through the proxy. The RAG pipeline's own retrieval
already uses a free local embedding model (Chroma's default, all-MiniLM-L6-v2) for a
different purpose (semantic search, not judging) - reusing it for ragas would be a separate
decision, not made here since a demo-scale eval script isn't worth adding a new proxy route
or a heavy local-embedding dev dependency for one metric. Revisit if answer relevance (not
just groundedness/retrieval quality) becomes something worth measuring.

Prereqs: a live litellm-proxy (see backend/.env.example) and an ingested RAG collection
(`uv run python -m app.rag.ingest` from backend/, if backend/chroma_db/ doesn't exist yet).

Run (from backend/, so `app` and the venv's dependencies are importable):
    uv run python ../evals/run_ragas_eval.py

Also writes the results to backend/app/data/eval_results/rag_eval.json (gitignored - a
generated artifact, not fixture data, same as backend/chroma_db/ or backend/staff.db) so the
backend's GET /ops/evals/rag route (see app/routes/evals.py) and the frontend's "Eval
Dashboard" staff tab can show the latest run without needing ragas/pytest as a production
dependency or a live proxy at request time - the dashboard only ever shows the last saved
snapshot, not a live re-run.
"""
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass

from app.gateway.client import GatewayError, get_chat_model  # noqa: E402
from app.graph.nodes.rag_policy_agent import _RETRIEVE_K, _synthesize  # noqa: E402
from app.rag.retriever import retrieve  # noqa: E402

_DATASET = Path(__file__).parent / "datasets" / "golden_qa.jsonl"
_RESULTS_PATH = _BACKEND_DIR / "app" / "data" / "eval_results" / "rag_eval.json"
_FAITHFULNESS_THRESHOLD = 0.8
_CONTEXT_PRECISION_THRESHOLD = 0.8


def _load_policy_questions() -> list[str]:
    with open(_DATASET, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    return [case["message"] for case in cases if case["expected_route"] == "rag_policy_agent"]


def _run_case(question: str) -> dict:
    """Mirrors rag_policy_agent's real retrieval+synthesis path exactly (same _RETRIEVE_K, same
    _synthesize call) so the scored context is the chunk that actually produced the answer -
    not just the top embedding match, which can differ now that _synthesize picks among several
    candidates (see docs/progress.md's Day 7 retrieval fix)."""
    chunks = retrieve(question, k=_RETRIEVE_K)
    if not chunks:
        raise RuntimeError(f"no policy chunk retrieved for {question!r} - can't score groundedness")

    answer, chunk = _synthesize(question, chunks)
    context = f'Source: {chunk.source}, section "{chunk.heading}"\n{chunk.text}'
    return {"user_input": question, "response": answer, "retrieved_contexts": [context]}


def _write_results(df, faithfulness_avg: float, context_precision_avg: float, passed: bool) -> None:
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {"faithfulness": _FAITHFULNESS_THRESHOLD, "context_precision": _CONTEXT_PRECISION_THRESHOLD},
        "averages": {
            "faithfulness": round(float(faithfulness_avg), 4),
            "context_precision": round(float(context_precision_avg), 4),
        },
        "passed": passed,
        "questions": [
            {
                "question": row["user_input"],
                "faithfulness": round(float(row["faithfulness"]), 4),
                "context_precision": round(float(row["llm_context_precision_without_reference"]), 4),
            }
            for _, row in df.iterrows()
        ],
    }
    _RESULTS_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nResults written to {_RESULTS_PATH}")


def main() -> int:
    warnings.filterwarnings("ignore", category=DeprecationWarning)  # ragas's LangchainLLMWrapper notice

    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference
    from ragas.run_config import RunConfig

    questions = _load_policy_questions()
    print(f"Running {len(questions)} rag_policy_agent question(s) through retrieval + synthesis...")

    try:
        samples = [_run_case(q) for q in questions]
    except GatewayError as exc:
        print(f"litellm-proxy unreachable - {exc}\nStart it first (see backend/.env.example).")
        return 1

    llm = LangchainLLMWrapper(get_chat_model())
    metrics = [Faithfulness(llm=llm), LLMContextPrecisionWithoutReference(llm=llm)]
    dataset = EvaluationDataset.from_list(samples)
    # ragas's default RunConfig bursts up to 16 concurrent judge calls - our single local
    # litellm-proxy process can't keep up with that and every faithfulness call (2 LLM calls
    # per sample) timed out at max_workers=16. A small pool matches what one proxy instance
    # handles reliably (individual calls are ~1-2s per docs/progress.md's latency notes).
    result = evaluate(
        dataset=dataset, metrics=metrics, llm=llm, show_progress=False, run_config=RunConfig(max_workers=2)
    )

    df = result.to_pandas()
    for _, row in df.iterrows():
        print(
            f"- {row['user_input']!r}\n"
            f"    faithfulness={row['faithfulness']:.2f}  "
            f"context_precision={row['llm_context_precision_without_reference']:.2f}"
        )

    faithfulness_avg = df["faithfulness"].mean()
    context_precision_avg = df["llm_context_precision_without_reference"].mean()
    print(f"\nAverage faithfulness: {faithfulness_avg:.2f} (threshold {_FAITHFULNESS_THRESHOLD})")
    print(f"Average context precision: {context_precision_avg:.2f} (threshold {_CONTEXT_PRECISION_THRESHOLD})")

    # bool(...) matters here: comparing numpy/pandas values yields numpy.bool_, which
    # json.dumps can't serialize (found by actually running _write_results, not by inspection).
    passed = bool(
        faithfulness_avg >= _FAITHFULNESS_THRESHOLD and context_precision_avg >= _CONTEXT_PRECISION_THRESHOLD
    )
    _write_results(df, faithfulness_avg, context_precision_avg, passed)

    if not passed:
        print("\nBelow threshold - see per-question scores above.")
        return 1
    print("\nAll thresholds met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
