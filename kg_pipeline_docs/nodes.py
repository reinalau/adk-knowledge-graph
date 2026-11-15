"""Function nodes for long documents. extract_chunks is a dynamic node (fan-out decided at run time)."""
import asyncio
import logging
import os
from dataclasses import asdict
from typing import Any

from google.adk import Event
from google.adk.agents.context import Context
from google.adk.workflow import node

from .llm_agents import create_chunk_extractor
from .merge import alias_map, apply_aliases, deterministic_aliases, merge_graphs, proper_name_candidates
from .splitter import Chunk, split_document

logger = logging.getLogger(__name__)

# Bound concurrent Bedrock calls (dynamic nodes are not limited by Workflow.max_concurrency).
MAX_PARALLEL_EXTRACTORS = int(os.getenv("DOCS_MAX_PARALLEL_EXTRACTORS", "5"))
EXTRACTION_ATTEMPTS = 2

# Session-scoped state (no prefix): every message in the same session extends the same graph.
# A new session starts an empty graph. A 'user:' prefix would share it across sessions instead.
ACCUMULATED_KEY = "kg_accumulated"
SOURCE_KEY = "kg_source_text"
TEXTS_KEY = "texts_processed"


def split_into_chunks(ctx: Context, node_input: str) -> str:
    """Entry node: split by sections and reset per-run state."""
    chunks, skipped = split_document(node_input)
    ctx.state["doc_chunks"] = [asdict(c) for c in chunks]
    ctx.state["skipped_chunks"] = skipped
    ctx.state["revisions"] = 0
    ctx.state["validation"] = ""
    sections = sorted({c.section for c in chunks})
    return f"{len(chunks)} chunks ({skipped} over the cap) from sections: {', '.join(sections)}"


async def extract_with_retry(run_node, index: int, chunk: Chunk, attempts: int = EXTRACTION_ATTEMPTS):
    """Run one chunk extractor; return its graph, or the last exception after `attempts` tries.

    ADK returns None when the model answers with empty text. That is treated as a failure,
    otherwise the chunk would be silently lost. An empty graph (empty lists) is a valid answer.
    """
    for attempt in range(1, attempts + 1):
        try:
            # A distinct run_id per attempt so the retry is a new child run.
            result = await run_node(
                create_chunk_extractor(index),
                node_input=chunk.labeled(),
                use_sub_branch=True,
                run_id=f"chunk{index}_attempt{attempt}",
            )
            if result is None:
                raise ValueError("empty model output")
            return result
        except Exception as exc:  # empty or invalid structured output, throttling, etc.
            logger.warning("extractor_%s attempt %s failed: %s", index, attempt, exc)
            if attempt == attempts:
                return exc


@node(name="extract_chunks", rerun_on_resume=True)
async def extract_chunks(ctx: Context, node_input: Any) -> list:
    """Dynamic fan-out: one extractor per chunk, run in parallel with a concurrency limit and one retry."""
    chunks = [Chunk(**c) for c in ctx.state.get("doc_chunks", [])]
    semaphore = asyncio.Semaphore(MAX_PARALLEL_EXTRACTORS)

    async def run(index: int, chunk: Chunk):
        async with semaphore:
            return await extract_with_retry(ctx.run_node, index, chunk)

    results = await asyncio.gather(*(run(i, c) for i, c in enumerate(chunks, start=1)))
    # One invalid model answer should not discard the whole document.
    ctx.state["failed_chunks"] = [i for i, r in enumerate(results, start=1) if isinstance(r, Exception)]
    return [r for r in results if not isinstance(r, Exception)]


def _current_source(ctx: Context) -> str:
    return "\n".join(c["text"] for c in ctx.state.get("doc_chunks", []))


def merge_partial_graphs(ctx: Context, node_input: list) -> str:
    """Union + safe aliases in code; the LLM only confirms proper-name candidates afterwards.

    Incremental: the graph accumulated by previous messages of the same session is merged first,
    so entities and aliases resolve across texts (e.g. 'Hinton' in text 2 -> 'Geoffrey Hinton' from text 1).
    """
    accumulated = ctx.state.get(ACCUMULATED_KEY)
    source = "\n".join(filter(None, [ctx.state.get(SOURCE_KEY, ""), _current_source(ctx)]))
    merged = merge_graphs([accumulated, *node_input])
    auto = deterministic_aliases(merged.entities, source)
    merged = apply_aliases(merged, auto)
    candidates = proper_name_candidates(merged.entities, source)

    ctx.state["kg_merged"] = merged.model_dump()
    ctx.state["auto_aliases"] = auto
    ctx.state["alias_candidates"] = [list(pair) for pair in candidates]
    ctx.state["candidate_pairs"] = "\n".join(f"{a} -> {b}" for a, b in candidates) or "(none)"
    return ctx.state["candidate_pairs"]


def apply_entity_aliases(ctx: Context, node_input: Any) -> dict:
    """Apply confirmed aliases to the merged graph (always from kg_merged, so it is idempotent).

    Only code-generated candidates are accepted, so an LLM cannot invent a merge
    (in testing, a free-form validator proposed 'mila -> montreal').
    """
    merged = ctx.state["kg_merged"]
    candidates = [tuple(p) for p in ctx.state.get("alias_candidates", [])]
    mapping = alias_map(node_input, merged["entities"], candidates)
    draft = apply_aliases(merged, mapping)

    present = set(draft.entities)
    remaining = [(a, b) for a, b in candidates if a in present and b in present]
    ctx.state["kg_draft"] = draft.model_dump()
    ctx.state["applied_aliases"] = {**ctx.state.get("auto_aliases", {}), **mapping}
    ctx.state["remaining_pairs"] = "\n".join(f"{a} -> {b}" for a, b in remaining) or "(none)"
    return draft.model_dump()


def accumulate_graph(ctx: Context, node_input: Any) -> Event:
    """Last node: save the published graph (unlinked entities included) for the next message."""
    final = ctx.state.get("kg_final") or {"entities": [], "relations": []}
    unlinked = ctx.state.get("unlinked_entities", [])
    accumulated = {"entities": [*final["entities"], *unlinked], "relations": final["relations"]}

    ctx.state[ACCUMULATED_KEY] = accumulated
    ctx.state[SOURCE_KEY] = "\n".join(filter(None, [ctx.state.get(SOURCE_KEY, ""), _current_source(ctx)]))
    ctx.state[TEXTS_KEY] = ctx.state.get(TEXTS_KEY, 0) + 1
    summary = (
        f"Accumulated graph after {ctx.state[TEXTS_KEY]} text(s): {len(final['entities'])} linked entities, "
        f"{len(final['relations'])} relations, {len(unlinked)} unlinked entities. "
        "Send another text in this session to extend it, or start a new session for a new graph."
    )
    return Event(output=accumulated, message=summary)
