"""Explicit agent registry; arbitrary dynamic agent/tool loading is forbidden."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from continucare.agents.contracts import SemanticResult
from continucare.agents.errors import AgentNotRegisteredError


class SemanticAgent(Protocol):
    def analyze(self, task: Any) -> SemanticResult: ...


@dataclass(frozen=True)
class RegisteredAgent:
    name: str
    version: str
    handler: SemanticAgent
    allowed_tools: tuple[str, ...] = ()


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, entry: RegisteredAgent) -> None:
        if entry.name in self._agents:
            raise ValueError(f"agent {entry.name!r} is already registered")
        self._agents[entry.name] = entry

    def get(self, name: str) -> RegisteredAgent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise AgentNotRegisteredError(f"agent {name!r} is not registered") from exc
