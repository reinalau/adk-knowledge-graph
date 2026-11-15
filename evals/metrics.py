"""Deterministic domain metrics (no LLM). Same idea as the Strands repo's evals/metrics.py."""
import re

from kg_pipeline.schemas import KnowledgeGraph


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("_", " ")).strip()


def _triples(relations) -> set[tuple[str, str, str]]:
    return {(_norm(r["subject"]), _norm(r["predicate"]), _norm(r["object"])) for r in relations}


def compute_precision_recall(predicted: KnowledgeGraph, expected: list[dict]) -> dict[str, float]:
    """Strict exact-match P/R/F1 over normalized triples (strict on purpose: a signal to compare models)."""
    pred = _triples([r.model_dump() for r in predicted.relations])
    gold = _triples(expected)
    hits = len(pred & gold)
    precision = hits / len(pred) if pred else 0.0
    recall = hits / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def entity_recall(predicted: KnowledgeGraph, expected_entities: list[str]) -> float:
    """Softer than triple match: share of expected entities found (substring match, lowercase)."""
    found = {_norm(e) for e in predicted.entities}
    if not expected_entities:
        return 0.0
    hits = sum(any(_norm(x) in f or f in _norm(x) for f in found) for x in expected_entities)
    return round(hits / len(expected_entities), 3)


def find_compound_entities(kg: KnowledgeGraph) -> list[str]:
    """Entities like 'A and B' that should have been split. Whole-word match ('Android' is fine)."""
    return [e for e in kg.entities if re.search(r"\b(and|y)\b", e, flags=re.IGNORECASE)]


def count_revisions(execution_order: list[str]) -> int:
    """How many times the router sent the draft back to the aggregator.

    Accepts plain node names or ADK node runs such as 'validator@2'.
    """
    validator_runs = sum(1 for node in execution_order if node.split("@")[0] == "validator")
    return max(validator_runs - 1, 0)
