"""Pydantic schemas shared by the pipeline (same contract as the Strands version)."""
from typing import Literal

from pydantic import BaseModel, Field


class Relation(BaseModel):
    subject: str = Field(description="Source entity of the triple.")
    predicate: str = Field(description="Relation between subject and object.")
    object: str = Field(description="Target entity of the triple.")


class KnowledgeGraph(BaseModel):
    entities: list[str] = Field(description="Unique, atomic entity names.")
    relations: list[Relation] = Field(description="Triples (subject, predicate, object).")

    def to_triple_lines(self) -> str:
        return "\n".join(f"{r.subject} | {r.predicate} | {r.object}" for r in self.relations)


class ValidatorOutput(BaseModel):
    format_ok: bool = Field(description="Triples are well formed and non-empty.")
    consistent: bool = Field(description="No contradictions or duplicated entities.")
    complete: bool = Field(description="Main facts of the source text are covered.")
    bias_free: bool = Field(description="No entity or source is over-represented.")
    issues: list[str] = Field(default_factory=list, description="Problems found, empty if none.")
    verdict: Literal["approved", "revision needed"]
