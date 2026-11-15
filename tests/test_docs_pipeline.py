"""Unit tests for the long-document version: no LLM, no network."""
import asyncio
from types import SimpleNamespace

from kg_pipeline.nodes import ROUTE_PUBLISH, ROUTE_REVISE
from kg_pipeline_docs.nodes import accumulate_graph, extract_with_retry, merge_partial_graphs
from kg_pipeline_docs.graph import build_workflow
from kg_pipeline_docs.merge import (
    alias_map,
    apply_aliases,
    defined_acronyms,
    deterministic_aliases,
    is_proper_name,
    merge_graphs,
    plural_aliases,
    proper_name_candidates,
)
from kg_pipeline_docs.splitter import Chunk, is_heading, split_document, split_sections

DOC = """Deep Learning Pioneers

Abstract
Geoffrey Hinton and Yann LeCun are pioneers of deep learning.

1 Introduction
Hinton developed backpropagation at the University of Toronto.

LeCun created convolutional neural networks at Bell Labs.

References
[1] Some cited paper. 2019.
"""


# ---------- splitter ----------
def test_is_heading():
    assert is_heading("Abstract") and is_heading("1 Introduction") and is_heading("2.1 Related Work")
    assert not is_heading("Hinton developed backpropagation at the University of Toronto.")
    assert not is_heading("")


def test_split_sections_strips_numbers():
    names = [name for name, _ in split_sections(DOC)]
    assert names == ["Abstract", "Introduction", "References"]


def test_split_document_skips_references():
    chunks, skipped = split_document(DOC)
    assert {c.section for c in chunks} == {"Abstract", "Introduction"}
    assert skipped == 0


def test_chunks_respect_word_limit_and_cap():
    long_text = "Intro\n" + "\n\n".join(f"Sentence number {i} has five words." for i in range(100))
    chunks, skipped = split_document(long_text, max_words=60, max_chunks=3)
    assert len(chunks) == 3 and skipped > 0
    assert all(len(c.text.split()) <= 60 for c in chunks)


# ---------- merge ----------
def _g(entities, *triples):
    return {"entities": entities, "relations": [{"subject": s, "predicate": p, "object": o} for s, p, o in triples]}


def test_merge_dedupes_and_adds_endpoints():
    merged = merge_graphs([
        _g(["Hinton"], ("Hinton", "received", "Turing Award")),
        _g(["hinton"], ("hinton ", "Received", "turing  award")),
    ])
    assert merged.entities == ["hinton", "turing award"]
    assert len(merged.relations) == 1


def test_alias_map_keeps_only_known_names():
    entities = ["hinton", "geoffrey hinton", "mila"]
    resolution = {"aliases": [
        {"alias": "Hinton", "canonical": "Geoffrey Hinton"},
        {"alias": "bengio", "canonical": "yoshua bengio"},  # not in the graph
        {"alias": "mila", "canonical": "mila"},  # no-op
    ]}
    assert alias_map(resolution, entities) == {"hinton": "geoffrey hinton"}


SOURCE = (
    "Geoffrey Hinton worked on artificial intelligence (AI). Later, Hinton left Google. "
    "Butterflies migrate. Many migratory butterflies and butterfly species were studied; species counts grew."
)


def test_plural_variants_are_merged_in_code():
    mapping = plural_aliases(["butterfly", "butterflies", "butterflies", "lines of evidence", "line of evidence"])
    assert mapping == {"butterfly": "butterflies", "line of evidence": "lines of evidence"}


def test_defined_acronyms():
    assert defined_acronyms(SOURCE) == {"ai": "artificial intelligence"}


def test_deterministic_aliases_never_merge_common_noun_containment():
    entities = ["species", "migratory species", "butterflies", "migratory butterflies", "ai", "artificial intelligence"]
    assert deterministic_aliases(entities, SOURCE) == {"ai": "artificial intelligence"}


def test_proper_name_detection_ignores_sentence_start():
    assert is_proper_name("hinton", SOURCE) and is_proper_name("google", SOURCE)
    assert not is_proper_name("butterflies", SOURCE)  # capitalized only at a sentence start


def test_proper_name_candidates_only_pair_proper_names():
    entities = ["geoffrey hinton", "hinton", "species", "migratory species", "google"]
    assert proper_name_candidates(entities, SOURCE) == [("hinton", "geoffrey hinton")]


def test_compound_entities_are_never_candidates():
    # Regression: Nova Pro extracted 'Pieridae and Nymphalidae' and the families got merged into it.
    source = "Data come from all families, all from the Pieridae and Nymphalidae, and from Europe, USA, and Australia."
    entities = ["pieridae", "nymphalidae", "pieridae and nymphalidae", "europe", "europe, usa, and australia"]
    assert not is_proper_name("pieridae and nymphalidae", source)
    assert proper_name_candidates(entities, source) == []


def test_connectors_allowed_in_proper_names():
    source = "He worked at the University of Toronto for years."
    assert is_proper_name("university of toronto", source)


def test_alias_map_rejects_pairs_outside_allowed_set():
    entities = ["deep learning", "ai research", "hinton", "geoffrey hinton"]
    resolution = {"aliases": [
        {"alias": "deep learning", "canonical": "ai research"},  # wrong merge proposed by the LLM
        {"alias": "hinton", "canonical": "geoffrey hinton"},
    ]}
    allowed = [("hinton", "geoffrey hinton")]
    assert alias_map(resolution, entities, allowed) == {"hinton": "geoffrey hinton"}


def test_apply_aliases_merges_relations_and_drops_self_loops():
    graph = _g(
        ["hinton", "geoffrey hinton", "google"],
        ("hinton", "left", "google"),
        ("geoffrey hinton", "left", "google"),
        ("hinton", "same as", "geoffrey hinton"),
    )
    result = apply_aliases(graph, {"hinton": "geoffrey hinton"})
    assert result.entities == ["geoffrey hinton", "google"]
    assert [(r.subject, r.object) for r in result.relations] == [("geoffrey hinton", "google")]


# ---------- extraction retry ----------
def _fake_run_node(*answers):
    """Async stand-in for ctx.run_node that returns (or raises) the given answers in order."""
    calls = []

    async def run_node(node, **kwargs):
        answer = answers[len(calls)]
        calls.append(kwargs["run_id"])
        if isinstance(answer, Exception):
            raise answer
        return answer

    return run_node, calls


CHUNK = Chunk(section="Abstract", text="Hinton left Google.")
GRAPH = {"entities": ["hinton", "google"], "relations": [{"subject": "hinton", "predicate": "left", "object": "google"}]}


def test_empty_output_is_retried():
    run_node, calls = _fake_run_node(None, GRAPH)  # None = model answered with empty text
    assert asyncio.run(extract_with_retry(run_node, 5, CHUNK)) == GRAPH
    assert calls == ["chunk5_attempt1", "chunk5_attempt2"]


def test_empty_output_twice_is_reported_as_failure():
    run_node, _ = _fake_run_node(None, None)
    assert isinstance(asyncio.run(extract_with_retry(run_node, 5, CHUNK)), ValueError)


def test_empty_graph_is_a_valid_answer():
    empty = {"entities": [], "relations": []}
    run_node, calls = _fake_run_node(empty)
    assert asyncio.run(extract_with_retry(run_node, 1, CHUNK)) == empty and len(calls) == 1


# ---------- incremental graph (same session) ----------
def _state_ctx(**state):
    return SimpleNamespace(state=dict(state))


def test_merge_includes_graph_from_previous_messages():
    previous = _g(["geoffrey hinton", "google"], ("geoffrey hinton", "left", "google"))
    ctx = _state_ctx(
        kg_accumulated=previous,
        kg_source_text="Geoffrey Hinton left Google.",
        doc_chunks=[{"section": "Abstract", "text": "Later, Hinton received the Turing Award."}],
    )
    new = _g(["hinton", "turing award"], ("hinton", "received", "turing award"))
    merge_partial_graphs(ctx, [new])
    merged = ctx.state["kg_merged"]
    assert {"geoffrey hinton", "hinton", "turing award", "google"} <= set(merged["entities"])
    # The proper-name candidate spans both texts.
    assert ["hinton", "geoffrey hinton"] in ctx.state["alias_candidates"]


def test_first_message_has_no_accumulated_graph():
    ctx = _state_ctx(doc_chunks=[{"section": "Abstract", "text": "Hinton left Google."}])
    merge_partial_graphs(ctx, [_g(["hinton", "google"], ("hinton", "left", "google"))])
    assert len(ctx.state["kg_merged"]["relations"]) == 1


def test_accumulate_keeps_unlinked_and_counts_texts():
    ctx = _state_ctx(
        kg_final=_g(["a", "b"], ("a", "knows", "b")),
        unlinked_entities=["pollinators"],
        doc_chunks=[{"section": "Abstract", "text": "Second text."}],
        kg_source_text="First text.",
        texts_processed=1,
    )
    accumulate_graph(ctx, None)
    assert ctx.state["kg_accumulated"]["entities"] == ["a", "b", "pollinators"]
    assert ctx.state["texts_processed"] == 2
    assert ctx.state["kg_source_text"] == "First text.\nSecond text."


# ---------- topology ----------
def test_docs_topology():
    edges = {(e.from_node.name, e.to_node.name, e.route) for e in build_workflow().graph.edges}
    assert ("split_into_chunks", "extract_chunks", None) in edges
    assert ("extract_chunks", "merge_partial_graphs", None) in edges
    assert ("route_verdict", "entity_resolver", ROUTE_REVISE) in edges
    assert ("route_verdict", "publish_graph", ROUTE_PUBLISH) in edges
    assert ("publish_graph", "accumulate_graph", None) in edges
