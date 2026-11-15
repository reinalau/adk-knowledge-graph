"""LLM nodes. Each one returns structured output (output_schema) and saves it in state (output_key)."""
from google.adk import Agent

from .config import GENERATE_CONFIG, get_model
from .nodes import NUM_EXTRACTORS
from .schemas import KnowledgeGraph, ValidatorOutput


def create_extractor(chunk_number: int) -> Agent:
    return Agent(
        name=f"extractor_{chunk_number}",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description=f"Extracts entities and relations from chunk {chunk_number}.",
        instruction=(
            f"You extract knowledge-graph triples ONLY from '--- CHUNK {chunk_number} ---'. "
            "Ignore every other chunk. If your chunk is '(empty)', return empty lists.\n"
            "Rules: entities must be atomic (never 'A and B'); use short, lowercase predicates; "
            "only include facts stated in the text."
        ),
        output_schema=KnowledgeGraph,
        output_key=f"triples_{chunk_number}",
    )


def create_aggregator() -> Agent:
    extractor_outputs = "\n".join(f"Extractor {i}: {{triples_{i}}}" for i in range(1, NUM_EXTRACTORS + 1))
    return Agent(
        name="aggregator",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description="Merges and deduplicates the extractors' triples.",
        instruction=(
            "You resolve conflicts between partial knowledge graphs. Merge all extractor outputs, "
            "remove duplicates, normalize entity names, and split any compound entity ('A and B') "
            "into two entities. Every subject and object used in a relation MUST also appear in "
            "'entities'.\n\n"
            f"{extractor_outputs}\n\n"
            "Validator feedback from the previous pass (empty on the first pass; if present, fix every issue):\n"
            "{validation?}"
        ),
        output_schema=KnowledgeGraph,
        output_key="kg_draft",
    )


def create_validator() -> Agent:
    return Agent(
        name="validator",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description="Checks format, consistency, completeness and bias of the merged graph.",
        instruction=(
            "Validate the knowledge graph you receive against the source chunks.\n"
            "Criteria: format_ok (well formed, non-empty), consistent (no contradictions, duplicate or "
            "compound entities), complete (main facts covered), bias_free (no entity or source over-represented).\n"
            "Set verdict to 'approved' only if ALL four are true; otherwise 'revision needed' and list "
            "concrete issues.\n\n"
            "Source chunks:\n{chunks}"
        ),
        output_schema=ValidatorOutput,
        output_key="validation",
    )
