"""Workflow for long documents: dynamic fan-out + merge in code + entity-resolution loop.

Incremental: each message in the same session extends the graph built by the previous ones.

START -> split_into_chunks -> extract_chunks (N extractors, dynamic) -> merge_partial_graphs (+ accumulated graph)
      -> entity_resolver -> apply_entity_aliases -> duplicate_validator -> route_verdict
              ^                                                                |
              +------------------------------ "revise" ------------------------+
                                              "publish" -> publish_graph -> accumulate_graph
"""
from google.adk import Workflow

from kg_pipeline.nodes import ROUTE_PUBLISH, ROUTE_REVISE, publish_graph, route_verdict

from .llm_agents import create_duplicate_validator, create_entity_resolver
from .nodes import accumulate_graph, apply_entity_aliases, extract_chunks, merge_partial_graphs, split_into_chunks


def build_workflow() -> Workflow:
    resolver = create_entity_resolver()
    return Workflow(
        name="kg_pipeline_docs",
        description="Incremental knowledge-graph extraction for long documents (dynamic fan-out by section).",
        edges=[
            (
                "START",
                split_into_chunks,
                extract_chunks,
                merge_partial_graphs,
                resolver,
                apply_entity_aliases,
                create_duplicate_validator(),
                route_verdict,  # reused: LLM verdict + deterministic consistency check + revision budget
            ),
            (route_verdict, {ROUTE_REVISE: resolver, ROUTE_PUBLISH: publish_graph}),
            (publish_graph, accumulate_graph),
        ],
    )
