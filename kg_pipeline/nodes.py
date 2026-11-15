"""Deterministic function nodes: plain Python, no LLM calls."""
import json
import re
from typing import Any

from google.adk import Event
from google.adk.agents.context import Context

from .schemas import KnowledgeGraph, ValidatorOutput

NUM_EXTRACTORS = 3
MAX_REVISIONS = 2

ROUTE_REVISE = "revise"
ROUTE_PUBLISH = "publish"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_sentences(text: str, num_chunks: int = NUM_EXTRACTORS) -> list[str]:
    """Split text into `num_chunks` consecutive chunks with a balanced number of sentences.

    Sentences are never cut. If the text has fewer sentences than chunks, the trailing chunks are empty.
    """
    sentences = [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]
    base, extra = divmod(len(sentences), num_chunks)
    chunks, start = [], 0
    for i in range(num_chunks):
        size = base + (1 if i < extra else 0)
        chunks.append(" ".join(sentences[start : start + size]))
        start += size
    return chunks


def split_text(ctx: Context, node_input: str) -> str:
    """Entry node: label the chunks and reset per-run state.

    Resetting matters when several messages are sent in the same session: without it, the next run
    would inherit the previous revision counter and validator feedback.
    """
    labeled = "\n\n".join(
        f"--- CHUNK {n} ---\n{chunk or '(empty)'}" for n, chunk in enumerate(chunk_sentences(node_input), start=1)
    )
    ctx.state["chunks"] = labeled
    ctx.state["revisions"] = 0
    ctx.state["validation"] = ""
    return labeled


def _as_dict(value: Any) -> dict:
    if isinstance(value, (KnowledgeGraph, ValidatorOutput)):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def find_missing_entities(graph: Any) -> list[str]:
    """Deterministic consistency check: relation endpoints that are not listed in `entities`.

    JSON Schema cannot express this rule, and a small LLM validator tends to approve it anyway.
    """
    graph = _as_dict(graph)
    known = {_norm(e) for e in graph.get("entities", [])}
    missing: dict[str, str] = {}
    for rel in graph.get("relations", []):
        for name in (rel.get("subject", ""), rel.get("object", "")):
            if name and _norm(name) not in known:
                missing.setdefault(_norm(name), name)
    return sorted(missing.values())


def route_verdict(ctx: Context, node_input: Any) -> Event:
    """Loop exit decided in code, not by the LLM.

    Publishes when the LLM validator approves AND the deterministic check passes, or when the
    revision budget is spent. Otherwise sends the draft back to the aggregator with the issues.
    """
    revisions = ctx.state.get("revisions", 0)
    validation = _as_dict(node_input)
    missing = find_missing_entities(ctx.state.get("kg_draft"))

    if (validation.get("verdict") == "approved" and not missing) or revisions >= MAX_REVISIONS:
        return Event(route=ROUTE_PUBLISH, output=ctx.state.get("kg_draft"))

    issues = list(validation.get("issues", []))
    if missing:
        issues.append(f"Add these relation endpoints to 'entities' (or drop the relation): {', '.join(missing)}")
    feedback = {**validation, "verdict": "revision needed", "issues": issues}
    if missing:
        feedback["consistent"] = False  # keep the feedback coherent with the code-detected issue
    # Overwrite the validator's state entry so the aggregator reads code + LLM issues via {validation?}.
    ctx.state["validation"] = feedback
    ctx.state["revisions"] = revisions + 1
    return Event(route=ROUTE_REVISE, output=feedback)


def split_unlinked(graph: KnowledgeGraph) -> tuple[KnowledgeGraph, list[str]]:
    """Separate entities that take part in no relation: they add nothing to the graph itself."""
    used = {_norm(name) for rel in graph.relations for name in (rel.subject, rel.object)}
    linked = [e for e in graph.entities if _norm(e) in used]
    unlinked = [e for e in graph.entities if _norm(e) not in used]
    return KnowledgeGraph(entities=linked, relations=graph.relations), unlinked


def publish_graph(ctx: Context, node_input: Any) -> Event:
    """Validate the approved draft and publish it (state + message shown in adk web).

    - Safety net: if the revision budget ran out with endpoints still missing, add them to
      `entities` so the published graph is always consistent.
    - Entities without relations are moved to `unlinked_entities`: the graph stays clean and
      nothing extracted is lost.
    """
    graph = KnowledgeGraph.model_validate(node_input)
    missing = find_missing_entities(graph)
    if missing:
        graph.entities.extend(missing)
    graph, unlinked = split_unlinked(graph)

    ctx.state["kg_final"] = graph.model_dump()
    ctx.state["unlinked_entities"] = unlinked
    ctx.state["entities_repaired"] = missing
    result = {"knowledge_graph": graph.model_dump(), "unlinked_entities": unlinked}
    return Event(output=graph.model_dump(), message=json.dumps(result, indent=2, ensure_ascii=False))
