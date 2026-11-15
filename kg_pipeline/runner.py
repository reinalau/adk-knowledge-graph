"""Programmatic execution of the workflows (used by main.py, main_docs.py and evals/)."""
import uuid
from dataclasses import dataclass, field
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import root_agent
from .schemas import KnowledgeGraph

APP_NAME = "kg_pipeline"


@dataclass
class PipelineResult:
    knowledge_graph: KnowledgeGraph | None
    execution_order: list[str] = field(default_factory=list)  # node runs, e.g. "aggregator@2"
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def revisions(self) -> int:
        return self.state.get("revisions", 0)


def _node_run(event) -> str | None:
    """Return 'node@iteration' for events that carry a node output, else None."""
    if event.output is None or not event.node_info or not event.node_info.path:
        return None
    return event.node_info.path.split("/")[-1]


async def run_session(texts: list[str], user_id: str = "user", agent: Any = None) -> list[PipelineResult]:
    """Send each text as a new message in ONE session and return one result per message.

    With kg_pipeline_docs, every message extends the graph accumulated in the session state.
    """
    agent = agent or root_agent
    app_name = getattr(agent, "name", APP_NAME)
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name=app_name, session_service=sessions)
    session_id = uuid.uuid4().hex
    await sessions.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    results = []
    for text in texts:
        order: list[str] = []
        message = types.Content(role="user", parts=[types.Part(text=text)])
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
            node_run = _node_run(event)
            if node_run and node_run not in order:
                order.append(node_run)

        session = await sessions.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        state = dict(session.state)
        final = state.get("kg_final")
        results.append(PipelineResult(
            knowledge_graph=KnowledgeGraph.model_validate(final) if final else None,
            execution_order=order,
            state=state,
        ))
    return results


async def run_pipeline(text: str, user_id: str = "user", agent: Any = None) -> PipelineResult:
    """Run a workflow once on `text` in a fresh session."""
    return (await run_session([text], user_id=user_id, agent=agent))[0]
