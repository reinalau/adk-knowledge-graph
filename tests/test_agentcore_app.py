"""Unit tests for the AgentCore adapter: input validation and response shape (no LLM, no AWS)."""
import pytest

from agentcore_app import MAX_INPUT_CHARS, build_response, validate_prompt


@pytest.mark.parametrize("payload", [None, "text", {}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 42}, {"text": "x"}])
def test_invalid_payloads_are_rejected(payload):
    with pytest.raises(ValueError):
        validate_prompt(payload)


def test_prompt_is_stripped():
    assert validate_prompt({"prompt": "  Hinton left Google.  "}) == "Hinton left Google."


def test_oversized_prompt_is_rejected():
    with pytest.raises(ValueError, match="exceeds"):
        validate_prompt({"prompt": "a" * (MAX_INPUT_CHARS + 1)})


def test_response_shape_with_empty_state():
    response = build_response({})
    assert response["knowledge_graph"] == {"entities": [], "relations": []}
    assert response["texts_processed"] == 0 and response["failed_chunks"] == []


def test_response_reads_session_state():
    graph = {"entities": ["a", "b"], "relations": [{"subject": "a", "predicate": "knows", "object": "b"}]}
    state = {"kg_final": graph, "unlinked_entities": ["c"], "texts_processed": 2, "doc_chunks": [{}, {}]}
    response = build_response(state)
    assert response["knowledge_graph"] == graph
    assert response["unlinked_entities"] == ["c"] and response["chunks"] == 2
