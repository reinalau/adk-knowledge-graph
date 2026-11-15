"""Graph-based Workflow (ADK 2.x).

START -> split_text -> extractor_1 -+
                    -> extractor_2 -+-> join_extractions -> aggregator -> validator -> route_verdict
                    -> extractor_3 -+                         ^                          |
                                                              +-------- "revise" --------+
                                                                        "publish" -> publish_graph
"""
from google.adk import Workflow
from google.adk.workflow import JoinNode

from .llm_agents import create_aggregator, create_extractor, create_validator
from .nodes import NUM_EXTRACTORS, ROUTE_PUBLISH, ROUTE_REVISE, publish_graph, route_verdict, split_text


def build_workflow() -> Workflow:
    extractors = [create_extractor(n) for n in range(1, NUM_EXTRACTORS + 1)]
    # Fan-in barrier: waits for ALL extractors (Strands Python needed a custom AND condition for this).
    join_extractions = JoinNode(name="join_extractions")
    aggregator = create_aggregator()

    return Workflow(
        name="kg_pipeline",
        description="Knowledge-graph extraction: parallel extraction, aggregation and a validation loop.",
        edges=[
            ("START", split_text),
            *[(split_text, extractor, join_extractions) for extractor in extractors],
            (join_extractions, aggregator, create_validator(), route_verdict),
            # Back-edge: a cycle must contain at least one routed edge, otherwise ADK rejects the graph.
            (route_verdict, {ROUTE_REVISE: aggregator, ROUTE_PUBLISH: publish_graph}),
        ],
    )
