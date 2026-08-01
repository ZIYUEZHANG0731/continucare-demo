"""Small controlled runtime with registry, timeout, idempotency and tool denial."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from continucare.agents.contracts import AgentRuntimeOutcome, SemanticTask
from continucare.agents.errors import AgentTimeoutError, AgentToolDeniedError
from continucare.agents.registry import AgentRegistry
from continucare.db import utc_now_iso


class AgentRuntime:
    def __init__(self, registry: AgentRegistry, *, timeout_seconds: float = 8.0):
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self._cache: dict[tuple[str, str], AgentRuntimeOutcome] = {}

    def run(
        self,
        agent_name: str,
        task: SemanticTask,
        *,
        requested_tools: tuple[str, ...] = (),
    ) -> AgentRuntimeOutcome:
        entry = self.registry.get(agent_name)
        denied = set(requested_tools) - set(entry.allowed_tools)
        if denied:
            raise AgentToolDeniedError(
                "agent tool call denied: " + ", ".join(sorted(denied))
            )

        cache_key = (agent_name, task.task_id)
        if cache_key in self._cache:
            return self._cache[cache_key].model_copy(update={"idempotent_replay": True})

        started_at = utc_now_iso()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cc-agent")
        future = executor.submit(entry.handler.analyze, task)
        try:
            result = future.result(timeout=self.timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise AgentTimeoutError(
                f"agent {agent_name!r} exceeded {self.timeout_seconds:.1f}s"
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        outcome = AgentRuntimeOutcome(
            result=result,
            started_at=started_at,
            completed_at=utc_now_iso(),
            agent_name=entry.name,
            agent_version=entry.version,
        )
        self._cache[cache_key] = outcome
        return outcome
