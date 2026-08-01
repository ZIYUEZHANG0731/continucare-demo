"""Application service for analyze → clarify/confirm → Layer-2 draft."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from continucare.agents.contracts import (
    AgentRunRecord,
    AgentRuntimeOutcome,
    AnswerOptionContract,
    CodingContract,
    EnableWhenContract,
    QuestionnaireItemContract,
    SemanticCandidate,
    SemanticResult,
    SemanticTask,
    Temporality,
)
from continucare.agents.registry import AgentRegistry, RegisteredAgent
from continucare.agents.runtime import AgentRuntime
from continucare.care_agent.agent import CareSemanticAgent
from continucare.care_agent.model_api import SemanticModelAdapter
from continucare.care_agent.safety import SafetyAgent
from continucare.care_engine import CareEngine
from continucare.db import utc_now_iso
from continucare.fhir.questionnaires import flatten_questionnaire_items
from continucare.services.audit import record_audit_event


SEMANTIC_ALIAS_EXTENSION_URL = (
    "urn:continucare:StructureDefinition:answer-semantic-alias"
)


class SemanticInteraction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: SemanticTask
    result: SemanticResult
    record: AgentRunRecord
    idempotent_replay: bool = False


class CareAgentService:
    """The only bridge from agents into CareEngine, gated by patient action."""

    def __init__(
        self,
        store,
        *,
        care_engine: CareEngine | None = None,
        model_adapter: SemanticModelAdapter | None = None,
    ):
        self.store = store
        self.care_engine = care_engine or CareEngine(store)
        self.safety = SafetyAgent()
        self.agent = CareSemanticAgent(model_adapter=model_adapter, safety=self.safety)
        registry = AgentRegistry()
        registry.register(
            RegisteredAgent(
                name="care_agent",
                version=self.agent.VERSION,
                handler=self.agent,
                allowed_tools=(),
            )
        )
        timeout = self.agent.model_adapter.config.timeout_seconds + 1.0
        self.runtime = AgentRuntime(registry, timeout_seconds=timeout)

    def analyze(self, session_id: str, message_text: str) -> SemanticInteraction:
        session = self._session(session_id)
        questionnaire = self.care_engine.questionnaire_for_session(session)
        task = self._build_task(session, questionnaire, message_text)
        existing = self.store.get_agent_run_by_task(task.task_id)
        if existing:
            return SemanticInteraction(
                task=task,
                result=SemanticResult.model_validate(existing.output_json),
                record=existing,
                idempotent_replay=True,
            )

        outcome = self.runtime.run("care_agent", task)
        record = self._record(task, outcome)
        self.store.save_agent_run(record)
        record_audit_event(
            self.store,
            patient_id=session.patient_id,
            entity_type="AgentRun",
            entity_id=record.run_id,
            event_type="semantic_analysis_completed",
            actor_type="controlled_care_agent",
            details={
                "session_id": session.session_id,
                "task_id": task.task_id,
                "mode": record.mode,
                "status": record.status,
                "candidate_link_ids": [
                    item.link_id for item in outcome.result.candidates
                ],
                "clarification_count": len(outcome.result.clarifications),
                "safety_violation_count": len(outcome.result.safety_violations),
                "candidate_issues": [
                    {
                        "link_id": issue.link_id,
                        "action": issue.action.value,
                        "reason_codes": issue.reason_codes,
                    }
                    for issue in outcome.result.candidate_issues
                ],
                "model_usage": outcome.result.model_usage,
                "provider_request_id": outcome.result.provider_request_id,
                "patient_confirmation_required": True,
            },
        )
        return SemanticInteraction(
            task=task,
            result=outcome.result,
            record=record,
            idempotent_replay=outcome.idempotent_replay,
        )

    def confirm_candidates(
        self, run_id: str, candidate_ids: list[str]
    ):
        if not candidate_ids:
            raise ValueError("请选择至少一项记录后再确认")
        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        unknown = set(candidate_ids) - set(available)
        if unknown:
            raise ValueError("确认内容不属于该次安全审核结果")
        candidates = [available[item_id] for item_id in candidate_ids]
        session = self._apply_confirmed(record, candidates)
        self._confirmation_audit(record, candidates, decision="accepted")
        return session

    def reject_candidates(self, run_id: str, candidate_ids: list[str]) -> None:
        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        candidates = [available[item_id] for item_id in candidate_ids if item_id in available]
        self._confirmation_audit(record, candidates, decision="rejected")

    def confirm_original_text(self, run_id: str):
        """Patient explicitly chooses to retain only their verbatim report."""

        record, result = self._stored_result(run_id)
        if result.status.value == "blocked":
            raise ValueError("指令型文本不能作为患者健康原话保存")
        session = self._apply_confirmed(record, [])
        self._confirmation_audit(record, [], decision="verbatim_only_accepted")
        return session

    def resolve_clarification(
        self, run_id: str, clarification_id: str, option_id: str
    ):
        record, result = self._stored_result(run_id)
        clarification = next(
            (
                item
                for item in result.clarifications
                if item.clarification_id == clarification_id
            ),
            None,
        )
        if clarification is None:
            raise ValueError("澄清问题不属于该次分析结果")
        option = next(
            (item for item in clarification.options if item.option_id == option_id),
            None,
        )
        if option is None:
            raise ValueError("澄清选项无效")
        candidate = clarification.proposed_candidate
        if not option.accepts_candidate or candidate is None:
            self._confirmation_audit(
                record,
                [candidate] if candidate else [],
                decision=f"clarification_{option_id}",
            )
            return self._session(record.session_id)

        if clarification.kind.value == "confirm_time_window":
            candidate = candidate.model_copy(
                update={"temporality": Temporality.EXPLICIT_24H}
            )
        elif clarification.kind.value == "confirm_current":
            candidate = candidate.model_copy(update={"temporality": Temporality.CURRENT})
        task = self._task_for_record(record)
        errors = self.safety.review_candidate(task, candidate)
        if errors:
            raise ValueError("澄清后的候选未通过安全校验")
        session = self._apply_confirmed(record, [candidate])
        self._confirmation_audit(record, [candidate], decision="clarification_accepted")
        return session

    def _apply_confirmed(
        self, record: AgentRunRecord, candidates: list[SemanticCandidate]
    ):
        session = self._session(record.session_id)
        if session.patient_id != record.patient_id:
            raise ValueError("Agent 运行记录与随访会话患者不一致")
        answers: dict[str, Any] = dict(session.answers)
        for candidate in candidates:
            answers[candidate.link_id] = candidate.answer
        original = record.input_text.strip()
        if original:
            previous = str(answers.get("free-text-report", "")).strip()
            lines = previous.splitlines() if previous else []
            if original not in lines:
                answers["free-text-report"] = "\n".join([*lines, original])
        return self.care_engine.save_draft(session.session_id, answers)

    def _confirmation_audit(self, record, candidates, *, decision: str) -> None:
        record_audit_event(
            self.store,
            patient_id=record.patient_id,
            entity_type="AgentRun",
            entity_id=record.run_id,
            event_type="semantic_candidate_patient_decision",
            actor_type="synthetic_patient",
            details={
                "session_id": record.session_id,
                "decision": decision,
                "candidate_ids": [item.candidate_id for item in candidates],
                "confirmed_link_ids": [item.link_id for item in candidates],
                "clinical_assessment": "not_assessed",
            },
        )

    def _record(
        self, task: SemanticTask, outcome: AgentRuntimeOutcome
    ) -> AgentRunRecord:
        config = self.agent.model_adapter.config
        return AgentRunRecord(
            run_id=outcome.result.run_id,
            task_id=task.task_id,
            patient_id=task.patient_id,
            session_id=task.session_id,
            agent_name=outcome.agent_name,
            agent_version=outcome.agent_version,
            mode=outcome.result.mode,
            input_text=task.message_text,
            input_hash=hashlib.sha256(task.message_text.encode("utf-8")).hexdigest(),
            output_json=outcome.result.model_dump(mode="json"),
            status=outcome.result.status.value,
            model_provider=(
                config.provider if self.agent.model_adapter.configured else None
            ),
            model_name=(
                config.model_name if self.agent.model_adapter.configured else None
            ),
            prompt_version=config.prompt_version,
            started_at=outcome.started_at,
            completed_at=outcome.completed_at,
        )

    def _stored_result(self, run_id: str):
        record = self.store.get_agent_run(run_id)
        if record is None:
            raise ValueError("Agent 运行记录不存在")
        return record, SemanticResult.model_validate(record.output_json)

    def _task_for_record(self, record: AgentRunRecord) -> SemanticTask:
        session = self._session(record.session_id)
        questionnaire = self.care_engine.questionnaire_for_session(session)
        return self._build_task(
            session,
            questionnaire,
            record.input_text,
            task_id=record.task_id,
        )

    def _session(self, session_id: str):
        session = self.store.get_care_session(session_id)
        if session is None:
            raise ValueError("随访会话不存在")
        if session.status.value != "in_progress":
            raise ValueError("只有进行中的随访可以使用对话辅助填写")
        return session

    def _build_task(
        self,
        session,
        questionnaire: dict[str, Any],
        message_text: str,
        *,
        task_id: str | None = None,
    ) -> SemanticTask:
        message_text = message_text.strip()
        digest = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
        task_id = task_id or f"semantic-{uuid5(NAMESPACE_URL, session.session_id + '|' + digest).hex}"
        return SemanticTask(
            task_id=task_id,
            patient_id=session.patient_id,
            session_id=session.session_id,
            pathway_code=session.pathway_code,
            pathway_version=session.pathway_version,
            questionnaire_canonical=session.questionnaire_canonical,
            questionnaire_version=session.questionnaire_version,
            message_text=message_text,
            existing_answers=session.answers,
            allowed_items=_questionnaire_contracts(questionnaire),
            created_at=utc_now_iso(),
        )


def _questionnaire_contracts(questionnaire: dict[str, Any]):
    contracts = []
    for item in flatten_questionnaire_items(questionnaire.get("item", [])):
        if item.get("type") in {"display", "group"}:
            continue
        contracts.append(
            QuestionnaireItemContract(
                link_id=item["linkId"],
                item_type=item["type"],
                text=item.get("text", item["linkId"]),
                codes=[CodingContract.model_validate(code) for code in item.get("code", [])],
                answer_options=[
                    AnswerOptionContract(
                        code=option["valueCoding"]["code"],
                        system=option["valueCoding"]["system"],
                        display=option["valueCoding"].get("display"),
                        semantic_aliases=[
                            extension["valueString"]
                            for extension in option.get("extension", [])
                            if extension.get("url") == SEMANTIC_ALIAS_EXTENSION_URL
                            and isinstance(extension.get("valueString"), str)
                        ],
                    )
                    for option in item.get("answerOption", [])
                    if "valueCoding" in option
                ],
                enable_when=[
                    EnableWhenContract(
                        question=condition["question"],
                        operator=condition["operator"],
                        answer=_enable_when_answer(condition),
                    )
                    for condition in item.get("enableWhen", [])
                ],
                enable_behavior=item.get("enableBehavior"),
                required=item.get("required", False),
                repeats=item.get("repeats", False),
            )
        )
    return contracts


def _enable_when_answer(condition: dict[str, Any]) -> Any:
    answers = [
        value for key, value in condition.items() if key.startswith("answer")
    ]
    if len(answers) != 1:
        raise ValueError("Questionnaire.enableWhen must contain one answer[x]")
    return answers[0]
