"""Behavior evals: runs the real pipeline (spends tokens) and reports deterministic metrics.

    python -m evals.run_evals            # all cases
    python -m evals.run_evals pioneers   # one case

For ADK's native evaluator (`adk eval`) see the project README.
"""
import asyncio
import json
import sys
from pathlib import Path

from evals.metrics import compute_precision_recall, count_revisions, entity_recall, find_compound_entities
from kg_pipeline.runner import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "test_cases" / "kg_extraction_cases.json"


async def evaluate(case: dict) -> dict:
    result = await run_pipeline(case["input"])
    kg = result.knowledge_graph
    if kg is None:
        return {"id": case["id"], "error": "no knowledge graph produced", "execution_order": result.execution_order}
    return {
        "id": case["id"],
        "execution_order": result.execution_order,
        "revisions": count_revisions(result.execution_order),
        "entities": len(kg.entities),
        "relations": len(kg.relations),
        **compute_precision_recall(kg, case["expected_relations"]),
        "entity_recall": entity_recall(kg, case["expected_entities"]),
        "compound_entities": find_compound_entities(kg),
    }


async def main(selected: set[str]) -> None:
    cases = [c for c in json.loads(CASES.read_text(encoding="utf-8")) if not selected or c["id"] in selected]
    rows = []
    for case in cases:
        row = await evaluate(case)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    out = ROOT / "outputs" / "eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {out}")


if __name__ == "__main__":
    asyncio.run(main(set(sys.argv[1:])))
