"""Schemas specific to the long-document version. KnowledgeGraph is reused from kg_pipeline."""
from typing import Literal

from pydantic import BaseModel, Field


class EntityAlias(BaseModel):
    alias: str = Field(description="Entity name to replace, exactly as it appears in the list.")
    canonical: str = Field(description="Preferred name, exactly as it appears in the list.")


class EntityResolution(BaseModel):
    aliases: list[EntityAlias] = Field(description="Pairs of names that refer to the same thing. Empty if none.")


class DuplicateCheck(BaseModel):
    duplicates: list[EntityAlias] = Field(
        default_factory=list, description="Remaining pairs that refer to the same thing, empty if none."
    )
    issues: list[str] = Field(default_factory=list, description="Short description of each remaining duplicate.")
    verdict: Literal["approved", "revision needed"]
