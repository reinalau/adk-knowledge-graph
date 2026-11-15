"""Unit tests: no LLM, no network."""
from types import SimpleNamespace

import pytest
from google.adk import Event
from pydantic import ValidationError

from kg_pipeline import config
from kg_pipeline.graph import build_workflow
from kg_pipeline.nodes import (
    MAX_REVISIONS,
    NUM_EXTRACTORS,
    ROUTE_PUBLISH,
    ROUTE_REVISE,
    chunk_sentences,
    find_missing_entities,
    publish_graph,
    route_verdict,
    split_text,
)
from kg_pipeline.schemas import KnowledgeGraph, Relation, ValidatorOutput

DRAFT = {"entities": ["a", "b"], "relations": [{"subject": "a", "predicate": "knows", "object": "b"}]}


def _ctx(**state):
    return SimpleNamespace(state=dict(state))


# ---------- schemas ----------
def test_relation_requires_all_fields():
    with pytest.raises(ValidationError):
        Relation.model_validate({})  # the empty object Nova Lite (v1) produced


def test_triple_lines():
    kg = KnowledgeGraph(entities=["a", "b"], relations=[Relation(subject="a", predicate="knows", object="b")])
    assert kg.to_triple_lines() == "a | knows | b"


def test_validator_verdict_is_restricted():
    with pytest.raises(ValidationError):
        ValidatorOutput(format_ok=True, consistent=True, complete=True, bias_free=True, verdict="maybe")


# ---------- split_text ----------
def test_chunks_are_balanced_and_keep_sentences():
    text = "One. Two. Three. Four. Five."
    assert chunk_sentences(text) == ["One. Two.", "Three. Four.", "Five."]


def test_short_text_leaves_trailing_chunks_empty():
    assert chunk_sentences("Only one sentence.") == ["Only one sentence.", "", ""]


def test_split_text_resets_per_run_state():
    ctx = _ctx(revisions=2, validation={"verdict": "revision needed"})
    labeled = split_text(ctx, "One. Two. Three.")
    assert labeled.count("--- CHUNK") == NUM_EXTRACTORS
    assert ctx.state["revisions"] == 0 and ctx.state["validation"] == ""
    assert ctx.state["chunks"] == labeled


# ---------- route_verdict ----------
def test_route_publishes_when_approved():
    event = route_verdict(_ctx(kg_draft=DRAFT), ValidatorOutput(
        format_ok=True, consistent=True, complete=True, bias_free=True, verdict="approved"))
    assert event.actions.route == ROUTE_PUBLISH and event.output == DRAFT


def test_route_revises_and_counts():
    ctx = _ctx(kg_draft=DRAFT, revisions=0)
    event = route_verdict(ctx, {"verdict": "revision needed", "issues": ["x"]})
    assert event.actions.route == ROUTE_REVISE and ctx.state["revisions"] == 1


def test_route_stops_at_revision_budget():
    ctx = _ctx(kg_draft=DRAFT, revisions=MAX_REVISIONS)
    event = route_verdict(ctx, {"verdict": "revision needed"})
    assert event.actions.route == ROUTE_PUBLISH


# ---------- deterministic consistency check ----------
INCONSISTENT = {
    "entities": ["Geoffrey Hinton"],
    "relations": [{"subject": "geoffrey  hinton", "predicate": "received", "object": "Turing Award"}],
}
APPROVED = {"verdict": "approved", "issues": []}


def test_find_missing_entities_normalizes_case_and_spaces():
    assert find_missing_entities(INCONSISTENT) == ["Turing Award"]
    assert find_missing_entities(DRAFT) == []


def test_route_overrides_llm_approval_when_inconsistent():
    ctx = _ctx(kg_draft=INCONSISTENT, revisions=0)
    event = route_verdict(ctx, APPROVED)
    assert event.actions.route == ROUTE_REVISE
    feedback = ctx.state["validation"]
    assert feedback["verdict"] == "revision needed"
    assert feedback["consistent"] is False
    assert "Turing Award" in feedback["issues"][-1]


def test_route_publishes_inconsistent_draft_when_budget_spent():
    ctx = _ctx(kg_draft=INCONSISTENT, revisions=MAX_REVISIONS)
    assert route_verdict(ctx, APPROVED).actions.route == ROUTE_PUBLISH


# ---------- publish_graph ----------
def test_publish_validates_and_stores():
    ctx = _ctx()
    event = publish_graph(ctx, DRAFT)
    assert isinstance(event, Event) and ctx.state["kg_final"] == DRAFT
    assert ctx.state["entities_repaired"] == []


def test_publish_moves_unlinked_entities_out_of_the_graph():
    ctx = _ctx()
    publish_graph(ctx, {**DRAFT, "entities": ["a", "b", "pollinators"]})
    assert ctx.state["kg_final"]["entities"] == ["a", "b"]
    assert ctx.state["unlinked_entities"] == ["pollinators"]


def test_publish_repairs_missing_entities():
    ctx = _ctx()
    publish_graph(ctx, INCONSISTENT)
    assert "Turing Award" in ctx.state["kg_final"]["entities"]
    assert ctx.state["entities_repaired"] == ["Turing Award"]


def test_publish_rejects_empty_relations():
    with pytest.raises(ValidationError):
        publish_graph(_ctx(), {"entities": ["a"], "relations": [{}]})


# ---------- topology ----------
def _edges():
    graph = build_workflow().graph
    return {(e.from_node.name, e.to_node.name, e.route) for e in graph.edges}


def test_fan_out_and_join():
    edges = _edges()
    for n in range(1, NUM_EXTRACTORS + 1):
        assert ("split_text", f"extractor_{n}", None) in edges
        assert (f"extractor_{n}", "join_extractions", None) in edges
    assert ("join_extractions", "aggregator", None) in edges


def test_validation_loop_is_routed():
    edges = _edges()
    assert ("route_verdict", "aggregator", ROUTE_REVISE) in edges
    assert ("route_verdict", "publish_graph", ROUTE_PUBLISH) in edges


# ---------- config ----------
def test_default_provider_is_bedrock(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
    assert config.get_model().model == "bedrock/us.amazon.nova-pro-v1:0"


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        config.get_model()


def test_invalid_provider(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "foo")
    with pytest.raises(ValueError):
        config.get_model()
