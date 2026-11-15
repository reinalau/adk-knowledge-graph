"""LLM nodes for long documents. Outputs stay small: per-chunk graphs, alias pairs and a verdict."""
from google.adk import Agent

from kg_pipeline.config import GENERATE_CONFIG, get_model
from kg_pipeline.schemas import KnowledgeGraph

from .schemas import DuplicateCheck, EntityResolution


def create_chunk_extractor(index: int) -> Agent:
    """One extractor per chunk, created at run time (the number of chunks depends on the document)."""
    return Agent(
        name=f"extractor_{index}",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description=f"Extracts entities and relations from chunk {index}.",
        instruction=(
            "You extract knowledge-graph triples from the document chunk you receive. The document can be "
            "from any field.\n"
            "Entities: nouns or noun phrases with their own meaning, such as people, organisms, "
            "organizations, places, methods, materials, concepts, datasets or quantities with their unit. "
            "Never use verbs, pronouns ('we', 'it', 'they') or generic words ('study', 'paper', 'results', "
            "'understanding', 'data') as entities. Keep entity names as written in the text, including "
            "capitalization. Entities must be atomic (never 'A and B').\n"
            "Relations: subject and object are entities; the predicate is a short verb phrase in lowercase. "
            "Prefer informative facts: findings, causes, comparisons and numbers (keep the number and its "
            "unit in the object, e.g. 'model X' 'reaches accuracy of' '92%').\n"
            "Only include facts stated in the text. Every subject and object must also appear in "
            "'entities'. Skip citations and figure or table references."
        ),
        output_schema=KnowledgeGraph,
    )


def create_entity_resolver() -> Agent:
    return Agent(
        name="entity_resolver",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description="Finds entity names that refer to the same thing.",
        instruction=(
            "You confirm entity aliases in a knowledge graph built from a long document.\n"
            "Each candidate line is 'alias -> canonical': a proper name contained in a longer proper name. "
            "Return a pair ONLY if both names refer to the same real-world thing in this document "
            "(e.g. a surname and the full name of the same person). Reject pairs that are different "
            "things sharing a word (e.g. a person and an organization named after them). "
            "Copy names exactly. Return an empty list if none is confirmed.\n\n"
            "Candidates:\n{candidate_pairs}\n\n"
            "Reviewer feedback from the previous pass (empty on the first pass). If it lists duplicates, "
            "you may also return those pairs:\n"
            "{validation?}"
        ),
        output_schema=EntityResolution,
    )


def create_duplicate_validator() -> Agent:
    return Agent(
        name="duplicate_validator",
        model=get_model(),
        generate_content_config=GENERATE_CONFIG,
        description="Reviews the resolved entity list for remaining duplicates.",
        instruction=(
            "These candidate pairs (a proper name contained in a longer proper name) were NOT merged in the "
            "knowledge graph. Report the ones that clearly refer to the same real-world thing. Do not report "
            "different things sharing a word. Return each one in 'duplicates' as (alias, canonical), copying "
            "names exactly. Set verdict to 'approved' if there are none; otherwise 'revision needed'.\n\n"
            "Unmerged candidates:\n{remaining_pairs}"
        ),
        output_schema=DuplicateCheck,
    )
