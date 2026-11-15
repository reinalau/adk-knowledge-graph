from evals.metrics import compute_precision_recall, count_revisions, entity_recall, find_compound_entities
from kg_pipeline.schemas import KnowledgeGraph, Relation

GOLD = [{"subject": "A", "predicate": "knows", "object": "B"}, {"subject": "B", "predicate": "knows", "object": "C"}]


def _kg(*triples, entities=()):
    return KnowledgeGraph(entities=list(entities), relations=[Relation(subject=s, predicate=p, object=o) for s, p, o in triples])


def test_perfect_match_ignores_case_and_underscores():
    kg = _kg(("a", "KNOWS", "b"), ("B", "knows", "c"))
    assert compute_precision_recall(kg, GOLD)["f1"] == 1.0


def test_no_overlap_and_empty():
    assert compute_precision_recall(_kg(("x", "y", "z")), GOLD)["f1"] == 0.0
    assert compute_precision_recall(_kg(), GOLD)["f1"] == 0.0


def test_partial_match():
    result = compute_precision_recall(_kg(("A", "knows", "B")), GOLD)
    assert result == {"precision": 1.0, "recall": 0.5, "f1": 0.667}


def test_entity_recall():
    assert entity_recall(_kg(entities=["Geoffrey Hinton"]), ["Hinton", "LeCun"]) == 0.5


def test_compound_entities_whole_word_only():
    kg = _kg(entities=["entities and relations", "Android", "Sandy", "Bell Labs"])
    assert find_compound_entities(kg) == ["entities and relations"]


def test_count_revisions():
    assert count_revisions(["aggregator@1", "validator@1", "publish_graph@1"]) == 0
    assert count_revisions(["aggregator@1", "validator@1", "aggregator@2", "validator@2", "publish_graph@1"]) == 1
    assert count_revisions(["validator", "validator"]) == 1  # plain names also work
