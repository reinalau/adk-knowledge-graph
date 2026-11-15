"""Amazon Bedrock AgentCore Runtime entry point for kg_pipeline_docs (incremental knowledge graph).

Contract: POST /invocations with {"prompt": "<text>"} and GET /ping (both provided by BedrockAgentCoreApp).

Session mapping: AgentCore runs each runtimeSessionId in its own microVM and keeps it alive between
invocations (15 min idle / 8 h max by default). The same id is used as the ADK session id, so
every text sent with the same runtimeSessionId extends the same graph held in memory.

Local test:
    python agentcore_app.py
    curl -X POST http://localhost:8080/invocations -H "Content-Type: application/json" \
         -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-session" -d '{"prompt": "..."}'
"""
import logging
import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from kg_pipeline_docs.agent import root_agent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("kg_agentcore")

APP_NAME = "kg_pipeline_docs"
USER_ID = "agentcore"
# Trust boundary: cap the input size so a single request cannot trigger an unbounded number of model calls.
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "60000"))

app = BedrockAgentCoreApp()
sessions = InMemorySessionService()  # lives as long as the AgentCore session microVM
runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=sessions)


def build_response(state: dict[str, Any]) -> dict[str, Any]:
    """Shape the session state into the API response."""
    return {
        "knowledge_graph": state.get("kg_final", {"entities": [], "relations": []}),
        "unlinked_entities": state.get("unlinked_entities", []),
        "texts_processed": state.get("texts_processed", 0),
        "chunks": len(state.get("doc_chunks", [])),
        "skipped_chunks": state.get("skipped_chunks", 0),
        "failed_chunks": state.get("failed_chunks", []),
        "revisions": state.get("revisions", 0),
    }


def validate_prompt(payload: Any) -> str:
    """Return the prompt text or raise ValueError with a client-facing message."""
    text = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("payload must be a JSON object with a non-empty 'prompt' string")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"'prompt' exceeds {MAX_INPUT_CHARS} characters; split the document and send it in parts")
    return text.strip()


@app.entrypoint
async def invoke(payload: Any, context: Any) -> dict[str, Any]:
    try:
        text = validate_prompt(payload)
    except ValueError as exc:
        return {"error": str(exc)}

    session_id = getattr(context, "session_id", None) or "local-session"
    if await sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id) is None:
        await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    message = types.Content(role="user", parts=[types.Part(text=text)])
    async for _ in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=message):
        pass

    session = await sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    response = build_response(dict(session.state))
    logger.info(
        "session=%s texts=%s relations=%s failed_chunks=%s",
        session_id, response["texts_processed"], len(response["knowledge_graph"]["relations"]),
        response["failed_chunks"],
    )
    return response


if __name__ == "__main__":
    app.run()
