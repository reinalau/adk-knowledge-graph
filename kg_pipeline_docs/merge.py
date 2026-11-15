"""Deterministic graph operations (no LLM), domain-agnostic so they work for any paper.

Alias policy:
- Merged in code: case and singular/plural variants, and acronyms defined in the text ("long form (LF)").
- Proposed in code, confirmed by the LLM: a proper name contained in a longer proper name
  ("Hinton" -> "Geoffrey Hinton"). Proper names are detected from the capitalization in the source text.
- Never merged: a common noun contained in another one ("species" vs "migratory species"),
  because a modifier can change the meaning in any domain.
"""
import re
from typing import Any, Iterable

from kg_pipeline.schemas import KnowledgeGraph, Relation

from .schemas import EntityResolution

_ACRONYM_DEF = re.compile(r"((?:[A-Za-z][\w\-]*\s+){1,8}?)\(([A-Z][A-Za-z0-9]{1,9})\)")
_SENTENCE_START = re.compile(r"(^|[.!?]\s+|\n\s*)$")
# Lowercase words allowed inside a proper name ('University of Toronto', 'Universidad de Buenos Aires').
_NAME_CONNECTORS = {"of", "the", "de", "del", "la", "las", "los", "el", "von", "van", "der", "da", "do", "du", "for"}
# Words that make an entity compound ('A and B'); compound entities are never alias candidates.
_CONJUNCTIONS = {"and", "or", "y", "o", "&"}


def is_compound(name: str) -> bool:
    words = norm(name).split()
    return "," in name or any(w in _CONJUNCTIONS for w in words)


def norm(name: str) -> str:
    return " ".join(name.lower().split())


def _as_graph(value: Any) -> KnowledgeGraph:
    return value if isinstance(value, KnowledgeGraph) else KnowledgeGraph.model_validate(value)


# ---------- build / merge ----------
def build_graph(entities: Iterable[str], relations: Iterable[tuple[str, str, str]]) -> KnowledgeGraph:
    """Build a consistent graph: dedupe entities and triples, add every relation endpoint to entities."""
    seen_entities: dict[str, None] = {}
    seen_triples: dict[tuple[str, str, str], None] = {}
    for e in entities:
        if norm(e):
            seen_entities.setdefault(norm(e))
    for s, p, o in relations:
        s, p, o = norm(s), norm(p), norm(o)
        if s and p and o and s != o:
            seen_triples.setdefault((s, p, o))
            seen_entities.setdefault(s)
            seen_entities.setdefault(o)
    return KnowledgeGraph(
        entities=list(seen_entities),
        relations=[Relation(subject=s, predicate=p, object=o) for s, p, o in seen_triples],
    )


def merge_graphs(graphs: Iterable[Any]) -> KnowledgeGraph:
    """Union of partial graphs. Code instead of an LLM, so it scales with the number of chunks."""
    graphs = [_as_graph(g) for g in graphs if g]
    return build_graph(
        (e for g in graphs for e in g.entities),
        ((r.subject, r.predicate, r.object) for g in graphs for r in g.relations),
    )


# ---------- deterministic aliases ----------
def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def form_key(name: str) -> str:
    """Case- and number-insensitive key. Every word is singularized, because the plural may be on
    the head noun ('lines of evidence' vs 'line of evidence'), not only on the last word."""
    return " ".join(_singular(word) for word in norm(name).split())


def plural_aliases(entities: Iterable[str]) -> dict[str, str]:
    """Map singular/plural variants to one form (the most frequent spelling wins; ties: the longer one)."""
    groups: dict[str, list[str]] = {}
    for e in entities:
        groups.setdefault(form_key(e), []).append(norm(e))
    mapping = {}
    for names in groups.values():
        unique = sorted(set(names), key=lambda n: (-names.count(n), -len(n)))
        for other in unique[1:]:
            mapping[other] = unique[0]
    return mapping


def defined_acronyms(source: str) -> dict[str, str]:
    """Acronyms defined in the text, e.g. 'artificial intelligence (AI)' -> {'ai': 'artificial intelligence'}."""
    found = {}
    for match in _ACRONYM_DEF.finditer(source):
        words, acronym = match.group(1).split(), match.group(2)
        letters = [c for c in acronym.lower() if c.isalpha()]
        long_form = words[-len(letters):] if len(words) >= len(letters) else []
        if long_form and [w[0].lower() for w in long_form] == letters:
            found[acronym.lower()] = norm(" ".join(long_form))
    return found


def deterministic_aliases(entities: Iterable[str], source: str) -> dict[str, str]:
    """Safe merges in any domain: singular/plural variants and acronyms defined in the text."""
    entities = [norm(e) for e in entities]
    known = set(entities)
    mapping = plural_aliases(entities)
    for acronym, long_form in defined_acronyms(source).items():
        long_form = mapping.get(long_form, long_form)
        if acronym in known and long_form in known and acronym != long_form:
            mapping[acronym] = long_form
    return mapping


# ---------- proper-name candidates (LLM confirms) ----------
def is_proper_name(name: str, source: str) -> bool:
    """True if the name appears capitalized in the source.

    A capital at a sentence start does not count for a single word ('Butterflies migrate.'), but a
    multi-word name with every content word capitalized does ('Geoffrey Hinton worked...').
    Compound entities ('Pieridae and Nymphalidae') are never proper names.
    """
    if is_compound(name):
        return False
    pattern = re.compile(r"\b" + r"\s+".join(map(re.escape, norm(name).split())) + r"\b", re.IGNORECASE)
    for match in pattern.finditer(source):
        words = match.group(0).split()
        content = [w for w in words if w.lower() not in _NAME_CONNECTORS]
        if not content or not all(w[0].isupper() for w in content):
            continue
        at_sentence_start = bool(_SENTENCE_START.search(source[: match.start()]))
        if not at_sentence_start or len(content) > 1:
            return True
    return False


def proper_name_candidates(entities: Iterable[str], source: str, limit: int = 60) -> list[tuple[str, str]]:
    """(alias, canonical) pairs where a proper name is contained in a longer proper name."""
    proper = sorted({norm(e) for e in entities if is_proper_name(e, source)}, key=len)
    pairs = []
    for i, short in enumerate(proper):
        for long in proper[i + 1 :]:
            if set(short.split()) < set(long.split()):
                pairs.append((short, long))
    return pairs[:limit]


# ---------- applying aliases ----------
def alias_map(
    resolution: Any, entities: Iterable[str], allowed: Iterable[tuple[str, str]] | None = None
) -> dict[str, str]:
    """Keep only valid pairs: both names in the graph, different, and (if given) in the allowed set."""
    if not isinstance(resolution, EntityResolution):
        resolution = EntityResolution.model_validate(resolution or {"aliases": []})
    known = {norm(e) for e in entities}
    allowed_set = None if allowed is None else {frozenset((norm(a), norm(b))) for a, b in allowed}
    mapping = {}
    for pair in resolution.aliases:
        alias, canonical = norm(pair.alias), norm(pair.canonical)
        if alias == canonical or alias not in known or canonical not in known:
            continue
        if allowed_set is not None and frozenset((alias, canonical)) not in allowed_set:
            continue
        mapping[alias] = canonical
    return mapping


def apply_aliases(graph: Any, mapping: dict[str, str]) -> KnowledgeGraph:
    """Rewrite aliases to their canonical name, then rebuild (drops duplicates and self-loops)."""
    graph = _as_graph(graph)

    def rename(name: str) -> str:
        return mapping.get(norm(name), norm(name))

    return build_graph(
        (rename(e) for e in graph.entities),
        ((rename(r.subject), r.predicate, rename(r.object)) for r in graph.relations),
    )
