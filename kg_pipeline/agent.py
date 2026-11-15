"""Entry point for `adk web` / `adk run` / `adk eval`: exposes root_agent."""
from .graph import build_workflow

root_agent = build_workflow()
