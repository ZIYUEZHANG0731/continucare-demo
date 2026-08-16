"""Application service for analyze → clarify/confirm → Layer-2 draft."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from continucare.agents.contracts import (
    AgentRunRecord,
    AgentRuntimeOutcome,
    AgentStageTrace,
    AnswerOptionContract,
    CodingContract,
    CandidateOrigin,
    CandidateSource,
    ClarificationKind,
    ClarificationOption,
    ClarificationRequest,
    ContextEvidenceBinding,
    ContextResolution,
    ContextResolutionDecision,
    ConversationContext,
    ConversationTurnContext,
    EnableWhenContract,
    LongTermMemoryItem,
    PendingActionContext,
    PendingActionType,
    QuestionnaireItemContract,
    ReportedSymptomMention,
    SemanticCandidate,
    SemanticResult,
    SemanticStatus,
    SemanticTask,
    SafetyReviewTask,
    SubjectType,
    Temporality,
    TemporalResolutionBasis,
    TerminologyMatchContract,
)
from continucare.agents.registry import AgentRegistry, RegisteredAgent
from continucare.agents.runtime import AgentRuntime
from continucare.care_agent.agent import CareSemanticAgent
from continucare.care_agent.mimo_enhancements import (
    LanguageRewriter,
    MiMoLanguageRewriter,
    MiMoSafetyCritic,
    SafetyCritic,
)
from continucare.care_agent.model_api import (
    SemanticModelAdapter,
    build_model_adapter,
)
from continucare.care_agent.numbers import (
    count_from_evidence,
    millilitres_from_evidence,
    parse_number_token,
)
from continucare.care_agent.safety import SafetyAgent
from continucare.care_agent.subjects import classify_evidence_subject
from continucare.care_agent.temporal import (
    build_temporal_context,
    candidate_temporal_resolution,
)
from continucare.care_engine import CareEngine
from continucare.config import get_settings
from continucare.db import utc_now_iso
from continucare.errors import ConcurrentWriteConflict
from continucare.fhir.questionnaires import (
    build_questionnaire_response,
    flatten_questionnaire_items,
    visible_questionnaire_items,
)
from continucare.services.audit import build_audit_event
from continucare.models import (
    AuditEvent,
    CareSession,
    ConfirmedAnswerContext,
    ConfirmedSymptomReport,
    ProvisionalAnswerContext,
    ProvisionalSymptomReport,
)
from continucare.fhir.terminology import UCUM
from continucare.terminology import (
    RepositoryTerminologyBackend,
    load_cn_glp1_terminology_catalog,
    terminology_catalog_sha256,
)
from continucare.terminology.catalog import (
    DYNAMIC_LINK_PREFIX,
    TerminologySearchBackend,
)


SEMANTIC_ALIAS_EXTENSION_URL = (
    "urn:continucare:StructureDefinition:answer-semantic-alias"
)


class SemanticInteraction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: SemanticTask
    result: SemanticResult
    record: AgentRunRecord
    idempotent_replay: bool = False


class DraftAnswerCorrection(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    correction_id: str
    link_id: str
    previous_answer: Any
    replacement_answer: Any
    previous_source_run_id: str
    correction_source_run_id: str
    raw_text: str


class DraftAnswerInvalidation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    link_id: str
    previous_answer: Any
    previous_source_run_id: str
    invalidated_by_link_ids: list[str]


class ConfirmedCandidatePlan(BaseModel):
    """Patient-confirmed Layer-3 material, validated but not yet persisted."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record: AgentRunRecord
    session: CareSession
    candidates: list[SemanticCandidate]
    answers: dict[str, Any]
    answer_contexts: list[ConfirmedAnswerContext]
    symptom_reports: list[ConfirmedSymptomReport]
    corrections: list[DraftAnswerCorrection] = Field(default_factory=list)
    invalidations: list[DraftAnswerInvalidation] = Field(default_factory=list)
    resolved_at: str


class ProvisionalCandidatePlan(BaseModel):
    """Model-organized material that still requires one final patient submit."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record: AgentRunRecord
    session: CareSession
    candidates: list[SemanticCandidate]
    answers: dict[str, Any]
    answer_contexts: list[ProvisionalAnswerContext]
    symptom_reports: list[ProvisionalSymptomReport]
    corrections: list[DraftAnswerCorrection] = Field(default_factory=list)
    invalidations: list[DraftAnswerInvalidation] = Field(default_factory=list)
    resolved_at: str


class CareAgentService:
    """The only bridge from agents into CareEngine, gated by patient action."""

    def __init__(
        self,
        store,
        *,
        care_engine: CareEngine | None = None,
        model_adapter: SemanticModelAdapter | None = None,
        safety_critic: SafetyCritic | None = None,
        language_rewriter: LanguageRewriter | None = None,
        patient_timezone: str | None = None,
        terminology_backend: TerminologySearchBackend | None = None,
    ):
        self.store = store
        self.care_engine = care_engine or CareEngine(store)
        selected_adapter = model_adapter or build_model_adapter()
        config = selected_adapter.config
        self.agent = CareSemanticAgent(model_adapter=selected_adapter)
        selected_critic = safety_critic or MiMoSafetyCritic(config)
        self.safety = SafetyAgent(critic=selected_critic)
        self.language_rewriter = language_rewriter or MiMoLanguageRewriter(config)
        self.patient_timezone = patient_timezone or get_settings().patient_timezone
        self.terminology = terminology_backend or RepositoryTerminologyBackend(
            load_cn_glp1_terminology_catalog()
        )
        registry = AgentRegistry()
        registry.register(
            RegisteredAgent(
                name="care_agent",
                version=self.agent.VERSION,
                handler=self.agent,
                allowed_tools=(),
            )
        )
        registry.register(
            RegisteredAgent(
                name="safety_agent",
                version=self.safety.VERSION,
                handler=self.safety,
                allowed_tools=(),
            )
        )
        timeout = self.agent.model_adapter.config.timeout_seconds * 2 + 2.0
        self.runtime = AgentRuntime(registry, timeout_seconds=timeout)

    def analyze(
        self,
        session_id: str,
        message_text: str,
        *,
        received_at: str | None = None,
        candidate_only: bool = False,
        focus_link_ids: list[str] | None = None,
    ) -> SemanticInteraction:
        return self._analyze(
            session_id,
            message_text,
            received_at=received_at,
            candidate_only=candidate_only,
            focus_link_ids=focus_link_ids,
            allow_completed_anchor=False,
            task_namespace=None,
        )

    def analyze_supplemental(
        self,
        session_id: str,
        message_text: str,
        *,
        received_at: str | None = None,
        focus_link_ids: list[str] | None = None,
    ) -> SemanticInteraction:
        """Prepare a candidate-only turn in a child supplemental occurrence."""

        session = self.store.get_care_session(session_id)
        if (
            session is None
            or session.status.value != "in_progress"
            or not session.parent_session_id
        ):
            raise ValueError("补充上报必须使用独立的进行中 occurrence")
        anchor = self.store.get_care_session(session.parent_session_id)
        if (
            anchor is None
            or anchor.status.value != "completed"
            or anchor.patient_id != session.patient_id
            or anchor.pathway_code != session.pathway_code
            or anchor.pathway_version != session.pathway_version
        ):
            raise ValueError("补充上报的已完成日随访锚点不可信")

        return self._analyze(
            session_id,
            message_text,
            received_at=received_at,
            candidate_only=True,
            focus_link_ids=focus_link_ids,
            allow_completed_anchor=False,
            task_namespace="supplemental",
        )

    def analyze_patient_checkin(
        self,
        session_id: str,
        message_text: str,
        *,
        received_at: str | None = None,
        focus_link_ids: list[str] | None = None,
    ) -> SemanticInteraction:
        """Prepare one product-chat draft turn under the composite catalog boundary."""

        return self._analyze(
            session_id,
            message_text,
            received_at=received_at,
            candidate_only=True,
            focus_link_ids=focus_link_ids,
            allow_completed_anchor=False,
            task_namespace="patient-checkin",
        )

    def _analyze(
        self,
        session_id: str,
        message_text: str,
        *,
        received_at: str | None,
        candidate_only: bool,
        focus_link_ids: list[str] | None,
        allow_completed_anchor: bool,
        task_namespace: str | None,
    ) -> SemanticInteraction:
        session = self._session(
            session_id, allow_completed_anchor=allow_completed_anchor
        )
        questionnaire = self.care_engine.questionnaire_for_session(session)
        task = self._build_task(
            session,
            questionnaire,
            message_text,
            received_at=received_at,
        )
        normalized_focus = sorted(set(focus_link_ids or []))
        focus_suffix = ""
        if normalized_focus:
            allowed_link_ids = {item.link_id for item in task.allowed_items}
            if any(link_id not in allowed_link_ids for link_id in normalized_focus):
                raise ValueError("聚焦采集字段不属于当前锁定问卷")
            focus_digest = hashlib.sha256(
                json.dumps(
                    normalized_focus, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()[:16]
            focus_suffix = f":focus:{focus_digest}"
            task = task.model_copy(
                update={
                    "task_id": f"{task.task_id}{focus_suffix}",
                    "focus_link_ids": normalized_focus,
                }
            )
        if task_namespace:
            task = task.model_copy(
                update={"task_id": f"{task_namespace}:{task.task_id}"}
            )
        latest_turn = (
            task.conversation_context.recent_turns[-1]
            if task.conversation_context.recent_turns
            else None
        )
        if latest_turn is not None and latest_turn.message_text == task.message_text:
            latest_record = self.store.get_agent_run(latest_turn.run_id)
            if latest_record is not None and self._record_matches_task(
                latest_record, task
            ) and (
                not task_namespace
                or latest_record.task_id.startswith(f"{task_namespace}:")
            ) and (not focus_suffix or latest_record.task_id.endswith(focus_suffix)):
                latest_result = SemanticResult.model_validate(
                    latest_record.output_json
                )
                latest_temporal = latest_result.temporal_context
                if (
                    latest_temporal is not None
                    and latest_temporal.local_date
                    == task.temporal_context.local_date
                ):
                    return self._verified_replay(
                        task.model_copy(update={"task_id": latest_record.task_id}),
                        latest_record,
                    )
        existing = self.store.get_agent_run_by_task(task.task_id)
        if existing:
            return self._verified_replay(task, existing)

        contextual = None if candidate_only else self._contextual_resolution(task)
        if contextual is not None:
            result, source_record, candidates, actions, decision, option_id = contextual
            now = utc_now_iso()
            outcome = AgentRuntimeOutcome(
                result=result,
                started_at=now,
                completed_at=now,
                agent_name="care_agent",
                agent_version=self.agent.VERSION,
            )
            record = self._record(task, outcome)
            if candidates:
                plan = self.prepare_confirmed_candidates(
                    source_record.run_id,
                    [],
                    resolved_at=record.completed_at,
                    _candidate_values=candidates,
                )
            else:
                current_session = self._session(source_record.session_id)
                plan = ConfirmedCandidatePlan(
                    record=source_record,
                    session=current_session,
                    candidates=[],
                    answers=current_session.answers,
                    answer_contexts=[],
                    symptom_reports=[],
                    resolved_at=record.completed_at,
                )
            resolution = result.context_resolution
            self._persist_conversation_decision(
                plan=plan,
                action_ids=[action.action_id for action in actions],
                decision=decision.value,
                resolution_decision=decision,
                option_id=option_id,
                audit_decision=(
                    "natural_language_context_accepted"
                    if candidates
                    else f"natural_language_context_{decision.value}"
                ),
                persist_answers=bool(candidates),
                response_run_id=record.run_id,
                response_text=task.message_text,
                response_record=record,
                context_audit_details=(
                    {
                        "session_id": task.session_id,
                        "source_run_id": resolution.source_run_id,
                        "action_ids": resolution.action_ids,
                        "decision": resolution.decision.value,
                        "applied_link_ids": resolution.applied_link_ids,
                        "followup_occurrence_id": (
                            task.temporal_context.followup_occurrence_id
                        ),
                        "received_at_local": task.temporal_context.received_at_local,
                    }
                    if resolution is not None
                    else None
                ),
            )
            return SemanticInteraction(task=task, result=result, record=record)

        contextual_draft = None if candidate_only else self._contextual_answer_candidate(task)
        if contextual_draft is None:
            if normalized_focus:
                started_at = utc_now_iso()
                focused_draft = self.agent.extract_focused(task, normalized_focus)
                if focused_draft is None:
                    raise ValueError("当前语义模型不支持聚焦问卷采集")
                care_outcome = AgentRuntimeOutcome(
                    result=focused_draft,
                    started_at=started_at,
                    completed_at=utc_now_iso(),
                    agent_name="care_agent",
                    agent_version=self.agent.VERSION,
                )
            else:
                care_outcome = self.runtime.run("care_agent", task)
        else:
            now = utc_now_iso()
            care_outcome = AgentRuntimeOutcome(
                result=contextual_draft,
                started_at=now,
                completed_at=now,
                agent_name="care_agent",
                agent_version=self.agent.VERSION,
            )
        draft = care_outcome.result.model_copy(
            update={
                "stage_traces": [
                    *care_outcome.result.stage_traces,
                    self._care_stage_trace(
                        care_outcome,
                        task=task,
                        focus_link_ids=normalized_focus,
                    ),
                ]
            }
        )
        draft = self._resolve_terminology(task, draft)
        safety_outcome = self.runtime.run(
            "safety_agent",
            SafetyReviewTask(
                task_id=f"{task.task_id}:safety",
                semantic_task=task,
                draft=draft,
            ),
        )
        result = self._resolve_missing_items(task, safety_outcome.result)
        if candidate_only:
            # Product chat owns the deterministic next-question policy.  Missing
            # Questionnaire items are therefore not persisted as conversational
            # actions here; every natural-language turn remains a candidate-only
            # proposal until the patient presses an explicit confirmation button.
            clarifications = [
                item
                for item in result.clarifications
                if not item.clarification_id.startswith("clarify-missing-")
            ]
            result = result.model_copy(
                update={
                    "clarifications": clarifications,
                    "status": (
                        SemanticStatus.NEEDS_CLARIFICATION
                        if clarifications
                        else (
                            SemanticStatus.NEEDS_CONFIRMATION
                            if result.candidates
                            else SemanticStatus.NO_MATCH
                        )
                    ),
                }
            )
        result = self._attach_temporal_context(task, result)
        result = self._rewrite_patient_language(task, result)
        result = result.model_copy(
            update={"model_usage": _aggregate_stage_usage(result.stage_traces)}
        )
        outcome = AgentRuntimeOutcome(
            result=result,
            started_at=care_outcome.started_at,
            completed_at=utc_now_iso(),
            agent_name=care_outcome.agent_name,
            agent_version=care_outcome.agent_version,
            idempotent_replay=care_outcome.idempotent_replay,
        )
        record = self._record(task, outcome)
        bindings_by_run: dict[str, set[str]] = {}
        for candidate in outcome.result.candidates:
            binding = candidate.context_binding
            if binding is not None:
                bindings_by_run.setdefault(binding.source_run_id, set()).add(
                    binding.source_action_id
                )
        if len(bindings_by_run) > 1:
            raise ValueError("一条回复不能同时关闭多个历史 Agent 运行的待办")
        analysis_audit = self._semantic_analysis_audit(task, record, outcome.result)
        if bindings_by_run and not candidate_only:
            source_run_id, bound_action_ids = next(iter(bindings_by_run.items()))
            source_record = self.store.get_agent_run(source_run_id)
            if source_record is None:
                raise ValueError("上下文候选引用的 Agent 运行记录不存在")
            current_session = self._session(source_record.session_id)
            self._persist_conversation_decision(
                plan=ConfirmedCandidatePlan(
                    record=source_record,
                    session=current_session,
                    candidates=[],
                    answers=current_session.answers,
                    answer_contexts=[],
                    symptom_reports=[],
                    resolved_at=record.completed_at,
                ),
                action_ids=sorted(bound_action_ids),
                decision=ContextResolutionDecision.ACCEPTED.value,
                resolution_decision=ContextResolutionDecision.ACCEPTED,
                option_id=None,
                audit_decision="context_binding_accepted",
                persist_answers=False,
                response_run_id=record.run_id,
                response_text=task.message_text,
                response_record=record,
                additional_audit_events=[analysis_audit],
            )
        else:
            self.store.persist_agent_run_bundle(
                record=record,
                audit_events=[analysis_audit],
            )
        return SemanticInteraction(
            task=task,
            result=outcome.result,
            record=record,
            idempotent_replay=outcome.idempotent_replay,
        )

    def _semantic_analysis_audit(
        self,
        task: SemanticTask,
        record: AgentRunRecord,
        result: SemanticResult,
    ) -> AuditEvent:
        """Build the deterministic audit fact owned by an AgentRun bundle."""

        event_id = "audit-" + uuid5(
            NAMESPACE_URL,
            f"{record.run_id}|semantic_analysis_completed",
        ).hex
        return build_audit_event(
            event_id=event_id,
            patient_id=record.patient_id,
            entity_type="AgentRun",
            entity_id=record.run_id,
            event_type="semantic_analysis_completed",
            actor_type="controlled_care_agent",
            created_at=record.completed_at,
            details={
                "session_id": record.session_id,
                "task_id": task.task_id,
                "mode": record.mode,
                "status": record.status,
                "candidate_link_ids": [
                    item.link_id for item in result.candidates
                ],
                "clarification_count": len(result.clarifications),
                "safety_violation_count": len(result.safety_violations),
                "candidate_issues": [
                    {
                        "link_id": issue.link_id,
                        "action": issue.action.value,
                        "reason_codes": issue.reason_codes,
                    }
                    for issue in result.candidate_issues
                ],
                "agent_stages": [
                    {
                        "stage": trace.stage,
                        "agent_name": trace.agent_name,
                        "mode": trace.mode,
                        "status": trace.status,
                        "prompt_version": trace.prompt_version,
                        "model_usage": trace.model_usage,
                        "latency_ms": trace.latency_ms,
                    }
                    for trace in result.stage_traces
                ],
                "model_usage": result.model_usage,
                "provider_request_id": result.provider_request_id,
                "patient_confirmation_required": True,
                "followup_occurrence_id": task.temporal_context.followup_occurrence_id,
                "patient_timezone": task.temporal_context.patient_timezone,
                "received_at_local": task.temporal_context.received_at_local,
            },
        )

    def _verified_replay(
        self, task: SemanticTask, record: AgentRunRecord
    ) -> SemanticInteraction:
        """Return only complete durable results; never bless an orphaned run."""

        self._require_record_boundary(record, task)
        result = SemanticResult.model_validate(record.output_json)
        if (
            record.patient_id != task.patient_id
            or record.session_id != task.session_id
            or result.run_id != record.run_id
            or result.task_id != record.task_id
        ):
            raise ConcurrentWriteConflict(
                "stored AgentRun identity is inconsistent; replay is blocked"
            )
        resolution = result.context_resolution
        if resolution is not None:
            complete = self.store.contextual_response_is_complete(
                record=record,
                source_run_id=resolution.source_run_id,
                action_ids=resolution.action_ids,
                decision=resolution.decision.value,
                applied_link_ids=resolution.applied_link_ids,
                require_context_audit=True,
            )
        else:
            complete = self.store.agent_run_has_audit(
                record, "semantic_analysis_completed"
            )
            bindings_by_run: dict[str, set[str]] = {}
            for candidate in result.candidates:
                binding = candidate.context_binding
                if binding is not None:
                    bindings_by_run.setdefault(binding.source_run_id, set()).add(
                        binding.source_action_id
                    )
            if len(bindings_by_run) > 1:
                complete = False
            elif bindings_by_run:
                source_run_id, action_ids = next(iter(bindings_by_run.items()))
                complete = complete and self.store.contextual_response_is_complete(
                    record=record,
                    source_run_id=source_run_id,
                    action_ids=sorted(action_ids),
                    decision=ContextResolutionDecision.ACCEPTED.value,
                    applied_link_ids=[],
                    require_context_audit=False,
                )
        if not complete:
            raise ConcurrentWriteConflict(
                "stored AgentRun is missing required decision or audit effects; "
                "replay is blocked"
            )
        return SemanticInteraction(
            task=task,
            result=result,
            record=record,
            idempotent_replay=True,
        )

    def _contextual_answer_candidate(
        self, task: SemanticTask
    ) -> SemanticResult | None:
        """Bind a concise value to one approved pending question without guessing."""

        actions = task.conversation_context.pending_actions
        if len(actions) != 1:
            return None
        action = actions[0]
        if (
            action.action_type != PendingActionType.CLARIFICATION
            or action.proposed_answer is not None
            or action.link_id is None
        ):
            return None
        question = next(
            (item for item in task.allowed_items if item.link_id == action.link_id),
            None,
        )
        if question is None:
            return None
        parsed = _contextual_answer(task.message_text, question)
        if parsed is None:
            return None
        answer, negated = parsed
        if action.link_id in {"nausea-present", "nausea-severity", "abdominal-pain-present"}:
            temporality = Temporality.CURRENT
        elif action.link_id in {
            "vomiting-count-24h",
            "fluid-intake-24h-estimated",
        }:
            temporality = Temporality.EXPLICIT_24H
        else:
            return None
        evidence = task.message_text
        display = _contextual_answer_display(answer, question)
        patient_message = f"我记录到您回答了“{question.text}”：{display}。请确认这项记录是否正确。"
        candidate = SemanticCandidate(
            candidate_id=(
                "candidate-"
                + uuid5(
                    NAMESPACE_URL,
                    f"{task.task_id}|{action.action_id}|{repr(answer)}",
                ).hex
            ),
            link_id=action.link_id,
            answer=answer,
            questionnaire_code=question.codes[0] if question.codes else None,
            evidence_text=evidence,
            evidence_start=0,
            evidence_end=len(evidence),
            subject=SubjectType.PATIENT,
            temporality=temporality,
            negated=negated,
            context_binding=ContextEvidenceBinding(
                source_run_id=action.source_run_id,
                source_action_id=action.action_id,
            ),
            patient_message=patient_message,
            template_id="confirm_contextual_answer",
        )
        return SemanticResult(
            run_id=f"run-{uuid5(NAMESPACE_URL, task.task_id + '|context-answer').hex}",
            task_id=task.task_id,
            status=SemanticStatus.NEEDS_CONFIRMATION,
            mode="deterministic_context_answer",
            care_agent_version=self.agent.VERSION,
            safety_agent_version="pending",
            language_policy_version="context-answer-v1",
            candidates=[candidate],
            stage_traces=[
                AgentStageTrace(
                    stage="conversation_context_binding",
                    agent_name="care_agent",
                    agent_version=self.agent.VERSION,
                    mode="deterministic_pending_question_binding",
                    status="bound",
                    details={
                        "source_run_id": action.source_run_id,
                        "source_action_id": action.action_id,
                        "link_id": action.link_id,
                    },
                )
            ],
            completed_at=utc_now_iso(),
        )

    def _contextual_resolution(self, task: SemanticTask):
        """Resolve only unambiguous short answers to one pending governed action."""

        actions = task.conversation_context.pending_actions
        if not actions:
            return None
        decision = _short_reply_decision(task.message_text, actions)
        if decision is None:
            return None
        selected = actions
        if len(actions) > 1 and not _all_actions_reply(task.message_text):
            return None
        source_run_ids = {item.source_run_id for item in selected}
        if len(source_run_ids) != 1:
            return None
        source_record = self.store.get_agent_run(source_run_ids.pop())
        if source_record is None:
            return None
        source_result = SemanticResult.model_validate(source_record.output_json)
        accepted: list[SemanticCandidate] = []
        option_id = None
        if decision == ContextResolutionDecision.ACCEPTED:
            for action in selected:
                if action.action_type == PendingActionType.CANDIDATE_CONFIRMATION:
                    candidate = next(
                        (
                            item
                            for item in source_result.candidates
                            if item.candidate_id == action.action_id
                        ),
                        None,
                    )
                else:
                    clarification = next(
                        (
                            item
                            for item in source_result.clarifications
                            if item.clarification_id == action.action_id
                        ),
                        None,
                    )
                    if clarification is None:
                        return None
                    option = next(
                        (
                            item
                            for item in clarification.options
                            if item.accepts_candidate
                        ),
                        None,
                    )
                    candidate = clarification.proposed_candidate
                    if option is None or candidate is None:
                        return None
                    option_id = option.option_id
                    if clarification.kind == ClarificationKind.CONFIRM_TIME_WINDOW:
                        candidate = candidate.model_copy(
                            update={"temporality": Temporality.EXPLICIT_24H}
                        )
                    elif clarification.kind == ClarificationKind.CONFIRM_CURRENT:
                        candidate = candidate.model_copy(
                            update={"temporality": Temporality.CURRENT}
                        )
                if candidate is None:
                    return None
                prior_temporal = source_result.temporal_context
                if prior_temporal is not None:
                    candidate = candidate.model_copy(
                        update={
                            "effective_time": candidate_temporal_resolution(
                                candidate,
                                prior_temporal,
                                basis=(
                                    TemporalResolutionBasis.PATIENT_CONFIRMATION
                                    if action.action_type
                                    == PendingActionType.CLARIFICATION
                                    else TemporalResolutionBasis.EXPLICIT_PATIENT_TEXT
                                ),
                                inherited_from_action_id=action.action_id,
                            )
                        }
                    )
                prior_task = self._task_for_record(source_record)
                if self.safety.review_candidate(prior_task, candidate):
                    return None
                accepted.append(candidate)
        elif decision == ContextResolutionDecision.REJECTED:
            option_id = "no"
        else:
            option_id = "unsure"

        applied_links = sorted({item.link_id for item in accepted})
        explanation = {
            ContextResolutionDecision.ACCEPTED: (
                "已将这句话作为对上一轮待确认内容的明确肯定回答处理。"
            ),
            ContextResolutionDecision.REJECTED: (
                "已将这句话作为对上一轮待确认内容的否定回答处理，未写入候选。"
            ),
            ContextResolutionDecision.UNSURE: (
                "已记录患者暂不确定，未写入候选。"
            ),
        }[decision]
        resolution = ContextResolution(
            decision=decision,
            source_run_id=source_record.run_id,
            action_ids=[item.action_id for item in selected],
            applied_link_ids=applied_links,
            response_text=task.message_text,
            explanation=explanation,
        )
        result = SemanticResult(
            run_id=f"run-{uuid5(NAMESPACE_URL, task.task_id + '|context').hex}",
            task_id=task.task_id,
            status=SemanticStatus.CONTEXT_RESOLVED,
            mode="deterministic_context_resolver",
            care_agent_version=self.agent.VERSION,
            safety_agent_version=self.safety.VERSION,
            language_policy_version="context-resolution-v1",
            temporal_context=task.temporal_context,
            context_resolution=resolution,
            stage_traces=[
                AgentStageTrace(
                    stage="conversation_context_resolution",
                    agent_name="care_agent",
                    agent_version=self.agent.VERSION,
                    mode="deterministic_pending_action_binding",
                    status=decision.value,
                    details={
                        "source_run_id": source_record.run_id,
                        "action_ids": resolution.action_ids,
                        "applied_link_ids": applied_links,
                    },
                )
            ],
            completed_at=utc_now_iso(),
        )
        return result, source_record, accepted, selected, decision, option_id

    def _resolve_terminology(
        self, task: SemanticTask, draft: SemanticResult
    ) -> SemanticResult:
        """Retrieve every code from the versioned catalog before Safety review."""

        decorated: list[SemanticCandidate] = []
        handled_concepts: set[str] = set()
        for candidate in sorted(
            draft.candidates,
            key=lambda item: item.link_id.startswith(DYNAMIC_LINK_PREFIX),
        ):
            is_dynamic = candidate.link_id.startswith(DYNAMIC_LINK_PREFIX)
            match = self.terminology.catalog.match_for_questionnaire(
                link_id=candidate.link_id,
                coding=candidate.questionnaire_code,
                matched_text=candidate.evidence_text,
            )
            if is_dynamic and match.concept_id in handled_concepts:
                continue
            candidate = candidate.model_copy(
                update={
                    "terminology_match": match,
                    "origin": (
                        CandidateOrigin.PATIENT_REPORTED_NEW
                        if is_dynamic
                        else candidate.origin
                    ),
                }
            )
            decorated.append(candidate)
            handled_concepts.add(match.concept_id)

        existing_clarifications: list[ClarificationRequest] = []
        for clarification in draft.clarifications:
            proposed = clarification.proposed_candidate
            if proposed is not None:
                match = self.terminology.catalog.match_for_questionnaire(
                    link_id=proposed.link_id,
                    coding=proposed.questionnaire_code,
                    matched_text=proposed.evidence_text,
                )
                proposed = proposed.model_copy(update={"terminology_match": match})
                clarification = clarification.model_copy(
                    update={"proposed_candidate": proposed}
                )
                handled_concepts.add(match.concept_id)
            existing_clarifications.append(clarification)

        if draft.status == SemanticStatus.BLOCKED:
            return draft.model_copy(
                update={
                    "candidates": decorated,
                    "clarifications": existing_clarifications,
                    "reported_symptom_mentions": [],
                    "stage_traces": [
                        *draft.stage_traces,
                        AgentStageTrace(
                            stage="terminology_resolution",
                            agent_name="terminology_resolver",
                            agent_version=(
                                f"repository-catalog-{self.terminology.catalog.version}"
                            ),
                            mode="blocked_input_no_lookup",
                            status="skipped",
                        ),
                    ],
                }
            )

        mentions_by_span: dict[tuple[int, int], ReportedSymptomMention] = {}
        for mention in draft.reported_symptom_mentions:
            key = (mention.evidence_start, mention.evidence_end)
            current = mentions_by_span.get(key)
            if current is None or (
                not self.terminology.search(current.symptom_text)
                and self.terminology.search(mention.symptom_text)
            ):
                mentions_by_span[key] = mention
        mentions = list(mentions_by_span.values())
        known_spans = {(item.evidence_start, item.evidence_end) for item in mentions}
        for start, end, _ in self.terminology.scan(task.message_text):
            evidence = task.message_text[start:end]
            identity = (start, end)
            if identity in known_spans:
                continue
            mentions.append(
                ReportedSymptomMention(
                    mention_id=(
                        "symptom-mention-"
                        + uuid5(
                            NAMESPACE_URL,
                            f"{task.task_id}|{start}|{end}|{evidence}",
                        ).hex
                    ),
                    symptom_text=evidence,
                    evidence_text=evidence,
                    evidence_start=start,
                    evidence_end=end,
                    subject=classify_evidence_subject(task.message_text, start, end),
                    temporality=_mention_temporality(task, start, end),
                    negated=_mention_negated(task.message_text, start),
                    source_mode=CandidateSource.DETERMINISTIC_CATALOG,
                )
            )
            known_spans.add(identity)

        new_clarifications: list[ClarificationRequest] = []
        unresolved: list[ReportedSymptomMention] = []
        emitted_concepts = set(handled_concepts)
        for mention in sorted(mentions, key=lambda item: item.evidence_start):
            if (
                mention.subject != SubjectType.PATIENT
                or mention.negated
                or mention.temporality == Temporality.HISTORICAL
            ):
                continue
            catalog_matches = self.terminology.search(mention.symptom_text)
            matches = [
                item
                for item in catalog_matches
                if item.concept_id not in handled_concepts
            ]
            if not matches:
                # Try the full verbatim span once; otherwise keep it for the
                # patient-visible, human-review path rather than inventing a code.
                evidence_matches = self.terminology.search(mention.evidence_text)
                catalog_matches = [*catalog_matches, *evidence_matches]
                matches = [
                    item
                    for item in evidence_matches
                    if item.concept_id not in handled_concepts
                ]
            matches = [
                item for item in matches if item.concept_id not in emitted_concepts
            ]
            matches = [
                item for item in matches if self._symptom_match_is_recordable(task, item)
            ]
            if not matches:
                if not catalog_matches:
                    unresolved.append(mention)
                continue
            if len(matches) > 1:
                new_clarifications.append(
                    self._terminology_clarification(task, mention, matches)
                )
                emitted_concepts.update(item.concept_id for item in matches)
                continue
            match = matches[0]
            candidate = self._symptom_candidate(task, mention, match)
            emitted_concepts.add(match.concept_id)
            if candidate.temporality == Temporality.UNSPECIFIED:
                new_clarifications.append(
                    ClarificationRequest(
                        clarification_id=(
                            "clarify-"
                            + uuid5(
                                NAMESPACE_URL,
                                f"{candidate.candidate_id}|current",
                            ).hex
                        ),
                        kind=ClarificationKind.CONFIRM_CURRENT,
                        prompt=(
                            f"我从术语目录匹配到“{match.preferred_zh}”。"
                            "这是您现在仍有的情况吗？"
                        ),
                        proposed_candidate=candidate,
                        reported_symptom=mention,
                        options=[
                            ClarificationOption(
                                option_id="yes_current",
                                label="是，现在仍有",
                                accepts_candidate=True,
                            ),
                            ClarificationOption(option_id="no", label="不是现在"),
                            ClarificationOption(option_id="unsure", label="不确定"),
                        ],
                    )
                )
            else:
                decorated.append(candidate)

        all_clarifications = [*existing_clarifications, *new_clarifications]
        if draft.status == SemanticStatus.BLOCKED:
            status = SemanticStatus.BLOCKED
        elif all_clarifications:
            status = SemanticStatus.NEEDS_CLARIFICATION
        elif decorated:
            status = SemanticStatus.NEEDS_CONFIRMATION
        else:
            status = SemanticStatus.NO_MATCH
        trace = AgentStageTrace(
            stage="terminology_resolution",
            agent_name="terminology_resolver",
            agent_version=f"repository-catalog-{self.terminology.catalog.version}",
            mode="deterministic_repository_lookup",
            status=status.value,
            details={
                "catalog_id": self.terminology.catalog.catalog_id,
                "catalog_version": self.terminology.catalog.version,
                "matched_candidate_count": len(decorated),
                "clarification_count": len(new_clarifications),
                "unresolved_mention_count": len(unresolved),
            },
        )
        ignored = list(draft.ignored_reasons)
        if unresolved:
            ignored.append(
                "存在未命中当前 GLP-1 术语目录的患者原话；已保留，待术语人员或医生复核。"
            )
        return draft.model_copy(
            update={
                "status": status,
                "candidates": decorated,
                "clarifications": all_clarifications,
                "reported_symptom_mentions": unresolved,
                "ignored_reasons": list(dict.fromkeys(ignored)),
                "stage_traces": [*draft.stage_traces, trace],
            }
        )

    def _symptom_match_is_recordable(
        self, task: SemanticTask, match: TerminologyMatchContract
    ) -> bool:
        binding = next(
            (
                item
                for item in self.terminology.catalog.questionnaire_bindings
                if item.symptom_concept_id == match.concept_id
            ),
            None,
        )
        if binding is None:
            return True
        question = next(
            (item for item in task.allowed_items if item.link_id == binding.link_id),
            None,
        )
        # A phrase alone cannot safely supply a count, quantity, or choice.
        return question is not None and question.item_type == "boolean"

    def _symptom_candidate(
        self,
        task: SemanticTask,
        mention: ReportedSymptomMention,
        match: TerminologyMatchContract,
        *,
        force_current: bool = False,
    ) -> SemanticCandidate:
        binding = next(
            (
                item
                for item in self.terminology.catalog.questionnaire_bindings
                if item.symptom_concept_id == match.concept_id
            ),
            None,
        )
        allowed = {item.link_id: item for item in task.allowed_items}
        if binding is not None and allowed[binding.link_id].item_type == "boolean":
            link_id = binding.link_id
            target_code = allowed[link_id].codes[0]
            origin = CandidateOrigin.PATHWAY_MONITORED
            match = self.terminology.catalog.match_for_questionnaire(
                link_id=link_id,
                coding=target_code,
                matched_text=mention.evidence_text,
            ).model_copy(
                update={
                    "matched_alias": match.matched_alias,
                    "match_method": match.match_method,
                }
            )
        else:
            link_id = f"{DYNAMIC_LINK_PREFIX}{match.concept_id}"
            target_code = match.target_coding
            origin = (
                CandidateOrigin.PATHWAY_MONITORED
                if binding is not None
                else CandidateOrigin.PATIENT_REPORTED_NEW
            )
        temporality = Temporality.CURRENT if force_current else mention.temporality
        time_label = {
            Temporality.CURRENT: "现在的",
            Temporality.EXPLICIT_24H: "过去24小时内的",
            Temporality.HISTORICAL: "此前的",
            Temporality.UNSPECIFIED: "",
        }[temporality]
        return SemanticCandidate(
            candidate_id=(
                "candidate-"
                + uuid5(
                    NAMESPACE_URL,
                    f"{task.task_id}|{mention.mention_id}|{match.concept_id}",
                ).hex
            ),
            link_id=link_id,
            answer=True,
            questionnaire_code=target_code,
            evidence_text=mention.evidence_text,
            evidence_start=mention.evidence_start,
            evidence_end=mention.evidence_end,
            subject=mention.subject,
            temporality=temporality,
            negated=False,
            patient_message=(
                f"我从 GLP-1 术语目录匹配到“{match.preferred_zh}”"
                f"（SNOMED CT {match.coding.code}），请确认这是您{time_label}情况。"
            ),
            template_id="confirm_terminology_match",
            source_mode=mention.source_mode,
            origin=origin,
            terminology_match=match,
        )

    def _terminology_clarification(
        self,
        task: SemanticTask,
        mention: ReportedSymptomMention,
        matches: list[TerminologyMatchContract],
    ) -> ClarificationRequest:
        options = [
            ClarificationOption(
                option_id=f"concept::{item.concept_id}",
                label=(
                    f"{item.preferred_zh}（确认是现在仍有）"
                    if mention.temporality == Temporality.UNSPECIFIED
                    else item.preferred_zh
                ),
                accepts_candidate=True,
                terminology_match=item,
            )
            for item in matches
        ]
        options.append(ClarificationOption(option_id="unsure", label="都不确定"))
        return ClarificationRequest(
            clarification_id=(
                "clarify-"
                + uuid5(
                    NAMESPACE_URL,
                    f"{task.task_id}|terminology|{mention.mention_id}",
                ).hex
            ),
            kind=ClarificationKind.TERMINOLOGY_DISAMBIGUATION,
            prompt=(
                f"您说的“{mention.evidence_text}”可能对应几种不同记录。"
                + (
                    "请选择最符合的一项，并同时确认这是您现在仍有的情况："
                    if mention.temporality == Temporality.UNSPECIFIED
                    else "请选择最符合的一项："
                )
            ),
            target_link_id=None,
            options=options,
            reported_symptom=mention,
        )

    def _attach_temporal_context(
        self, task: SemanticTask, result: SemanticResult
    ) -> SemanticResult:
        def enrich(candidate: SemanticCandidate) -> SemanticCandidate:
            binding = candidate.context_binding
            return candidate.model_copy(
                update={
                    "effective_time": candidate_temporal_resolution(
                        candidate,
                        task.temporal_context,
                        basis=(
                            TemporalResolutionBasis.PENDING_QUESTION
                            if binding is not None
                            else TemporalResolutionBasis.EXPLICIT_PATIENT_TEXT
                        ),
                        inherited_from_action_id=(
                            binding.source_action_id
                            if binding is not None
                            else None
                        ),
                    )
                }
            )

        candidates = [enrich(item) for item in result.candidates]
        clarifications = [
            item.model_copy(
                update={
                    "proposed_candidate": (
                        enrich(item.proposed_candidate)
                        if item.proposed_candidate is not None
                        else None
                    )
                }
            )
            for item in result.clarifications
        ]
        return result.model_copy(
            update={
                "candidates": candidates,
                "clarifications": clarifications,
                "temporal_context": task.temporal_context,
                "stage_traces": [
                    *result.stage_traces,
                    AgentStageTrace(
                        stage="temporal_context",
                        agent_name="care_agent",
                        agent_version=self.agent.VERSION,
                        mode="deterministic_patient_local_time",
                        status="resolved",
                        details={
                            "patient_timezone": task.temporal_context.patient_timezone,
                            "local_date": task.temporal_context.local_date,
                            "followup_occurrence_id": (
                                task.temporal_context.followup_occurrence_id
                            ),
                            "detected_mentions": [
                                item.expression
                                for item in task.temporal_context.detected_mentions
                            ],
                        },
                    ),
                ],
            }
        )

    def _care_stage_trace(
        self,
        outcome: AgentRuntimeOutcome,
        *,
        task: SemanticTask,
        focus_link_ids: list[str] | None = None,
    ) -> AgentStageTrace:
        result = outcome.result
        config = self.agent.model_adapter.config
        model_mode = result.mode in {
            "model_api:xiaomi_mimo",
            "model_api:volcengine_doubao",
            "model_api:feishu_aily_not_live_verified",
        }
        return AgentStageTrace(
            stage="care_extraction",
            agent_name="care_agent",
            agent_version=self.agent.VERSION,
            mode=result.mode,
            status=result.status.value,
            model_provider=config.provider if model_mode else None,
            model_name=config.model_name if model_mode else None,
            prompt_version=config.prompt_version if model_mode else None,
            model_usage=result.model_usage if model_mode else None,
            provider_request_id=result.provider_request_id if model_mode else None,
            latency_ms=_elapsed_ms(outcome.started_at, outcome.completed_at),
            details={
                "focus_link_ids": focus_link_ids or [],
                "memory_scope": task.conversation_context.memory_scope,
                "recent_turn_count": len(task.conversation_context.recent_turns),
                "focused_existing_answer_link_ids": [
                    link_id
                    for link_id in (focus_link_ids or [])
                    if link_id in task.existing_answers
                ],
            },
        )

    def _resolve_missing_items(
        self, task: SemanticTask, result: SemanticResult
    ) -> SemanticResult:
        supported = sorted(
            {
                item.link_id
                for item in result.missing_items
                if item.status.value == "supported"
            }
        )
        focused = None
        focused_attempt_failed = False
        if supported:
            try:
                focused = self.agent.extract_focused(task, supported)
            except Exception as exc:
                focused_attempt_failed = True
                result = result.model_copy(
                    update={
                        "ignored_reasons": [
                            *result.ignored_reasons,
                            f"focused_reextract_fallback:{type(exc).__name__}",
                        ],
                        "stage_traces": [
                            *result.stage_traces,
                            AgentStageTrace(
                                stage="focused_reextract",
                                agent_name="care_agent",
                                agent_version=self.agent.VERSION,
                                mode="questionnaire_clarification_fallback",
                                status="failed",
                                details={
                                    "requested_link_ids": supported,
                                    "error_type": type(exc).__name__,
                                },
                            ),
                        ],
                    }
                )
            if focused is None and not focused_attempt_failed:
                result = result.model_copy(
                    update={
                        "stage_traces": [
                            *result.stage_traces,
                            AgentStageTrace(
                                stage="focused_reextract",
                                agent_name="care_agent",
                                agent_version=self.agent.VERSION,
                                mode="questionnaire_clarification_fallback",
                                status="unavailable",
                                details={"requested_link_ids": supported},
                            ),
                        ]
                    }
                )
        if focused is not None:
            focused = focused.model_copy(
                update={
                    "candidates": [
                        item.model_copy(
                            update={
                                "terminology_match": (
                                    self.terminology.catalog.match_for_questionnaire(
                                        link_id=item.link_id,
                                        coding=item.questionnaire_code,
                                        matched_text=item.evidence_text,
                                    )
                                )
                            }
                        )
                        for item in focused.candidates
                    ],
                    "clarifications": [
                        clarification.model_copy(
                            update={
                                "proposed_candidate": (
                                    clarification.proposed_candidate.model_copy(
                                        update={
                                            "terminology_match": (
                                                self.terminology.catalog.match_for_questionnaire(
                                                    link_id=clarification.proposed_candidate.link_id,
                                                    coding=clarification.proposed_candidate.questionnaire_code,
                                                    matched_text=clarification.proposed_candidate.evidence_text,
                                                )
                                            )
                                        }
                                    )
                                    if clarification.proposed_candidate is not None
                                    else None
                                )
                            }
                        )
                        for clarification in focused.clarifications
                    ],
                }
            )
            existing_links = {item.link_id for item in result.candidates}
            merged = result.model_copy(
                update={
                    "candidates": [
                        *result.candidates,
                        *[
                            item
                            for item in focused.candidates
                            if item.link_id not in existing_links
                        ],
                    ],
                    "clarifications": [
                        *result.clarifications,
                        *focused.clarifications,
                    ],
                    "candidate_issues": [
                        *result.candidate_issues,
                        *focused.candidate_issues,
                    ],
                }
            )
            merged = self.safety.review(task, merged)
            resolved_links = {item.link_id for item in merged.candidates}
            resolved_links.update(
                item.proposed_candidate.link_id
                for item in merged.clarifications
                if item.proposed_candidate is not None
            )
            trace = AgentStageTrace(
                stage="focused_reextract",
                agent_name="care_agent",
                agent_version=self.agent.VERSION,
                mode=focused.mode,
                status=(
                    "resolved"
                    if set(supported) <= resolved_links
                    else "partial"
                ),
                model_provider=self.agent.model_adapter.config.provider,
                model_name=self.agent.model_adapter.config.model_name,
                prompt_version=self.agent.model_adapter.config.prompt_version,
                model_usage=focused.model_usage,
                provider_request_id=focused.provider_request_id,
                details={
                    "requested_link_ids": supported,
                    "resolved_link_ids": sorted(set(supported) & resolved_links),
                },
            )
            result = merged.model_copy(
                update={"stage_traces": [*result.stage_traces, trace]}
            )
        return self._append_missing_clarifications(task, result)

    def _append_missing_clarifications(
        self, task: SemanticTask, result: SemanticResult
    ) -> SemanticResult:
        represented = {item.link_id for item in result.candidates}
        represented.update(
            item.proposed_candidate.link_id
            for item in result.clarifications
            if item.proposed_candidate is not None
        )
        pending = [
            item
            for item in result.missing_items
            if item.status.value in {"supported", "ambiguous"}
            and item.link_id not in represented
        ]
        if not pending:
            return result
        questions = {item.link_id: item for item in task.allowed_items}
        clarifications = list(result.clarifications)
        for finding in pending:
            question = questions[finding.link_id]
            clarifications.append(
                ClarificationRequest(
                    clarification_id=f"clarify-missing-{task.task_id}-{finding.link_id}",
                    kind=ClarificationKind.MISSING_DEPENDENT_VALUE,
                    target_link_id=finding.link_id,
                    prompt=(
                        f"为了避免漏记，还需要请您确认：{question.text}？"
                    ),
                    options=[
                        ClarificationOption(
                            option_id="no",
                            label="在下方完整问卷中填写",
                        ),
                        ClarificationOption(
                            option_id="unsure",
                            label="我暂时不确定",
                        ),
                    ],
                )
            )
        return result.model_copy(
            update={
                "status": SemanticStatus.NEEDS_CLARIFICATION,
                "clarifications": clarifications,
            }
        )

    def _rewrite_patient_language(
        self, task: SemanticTask, result: SemanticResult
    ) -> SemanticResult:
        if not self.language_rewriter.configured or result.status == SemanticStatus.BLOCKED:
            trace = AgentStageTrace(
                stage="language_rewrite",
                agent_name="care_agent",
                agent_version=self.agent.VERSION,
                mode="deterministic_template",
                status="skipped",
            )
            return result.model_copy(
                update={"stage_traces": [*result.stage_traces, trace]}
            )
        try:
            outcome = self.language_rewriter.rewrite(task, result)
        except Exception as exc:
            trace = AgentStageTrace(
                stage="language_rewrite",
                agent_name="care_agent",
                agent_version=self.agent.VERSION,
                mode="deterministic_template_fallback",
                status="failed",
                details={"error_type": type(exc).__name__},
            )
            return result.model_copy(
                update={
                    "ignored_reasons": [
                        *result.ignored_reasons,
                        f"language_rewriter_fallback:{type(exc).__name__}",
                    ],
                    "stage_traces": [*result.stage_traces, trace],
                }
            )

        candidates = [
            item.model_copy(
                update={
                    "patient_message": outcome.rewrites.get(
                        item.candidate_id, item.patient_message
                    )
                }
            )
            for item in result.candidates
        ]
        clarifications = [
            item.model_copy(
                update={
                    "prompt": outcome.rewrites.get(
                        item.clarification_id, item.prompt
                    )
                }
            )
            for item in result.clarifications
        ]
        trace = AgentStageTrace(
            stage="language_rewrite",
            agent_name="care_agent",
            agent_version=self.agent.VERSION,
            mode=outcome.mode,
            status="completed",
            model_provider=self.agent.model_adapter.config.provider,
            model_name=self.agent.model_adapter.config.model_name,
            prompt_version=outcome.prompt_version,
            model_usage=outcome.model_usage,
            provider_request_id=outcome.provider_request_id,
            latency_ms=outcome.latency_ms,
            details={
                "rewritten_count": len(outcome.rewrites),
                "template_fallback_count": len(outcome.rejected_reasons),
                "fallback_reasons": outcome.rejected_reasons,
                "attempt_count": outcome.attempt_count,
            },
        )
        reasons = list(result.ignored_reasons)
        if outcome.rejected_reasons:
            reasons.append("language_rewriter_partial_template_fallback")
        return result.model_copy(
            update={
                "candidates": candidates,
                "clarifications": clarifications,
                "ignored_reasons": reasons,
                "stage_traces": [*result.stage_traces, trace],
            }
        )

    def confirm_candidates(
        self,
        run_id: str,
        candidate_ids: list[str],
        *,
        include_original_text: bool = True,
        track_original_text_context: bool = False,
        answer_overrides: dict[str, Any] | None = None,
    ):
        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(not item.strip() for item in candidate_ids)
        ):
            raise ValueError("请选择至少一项记录后再确认")
        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        unknown = set(candidate_ids) - set(available)
        if unknown:
            raise ValueError("确认内容不属于该次安全审核结果")
        candidates = [available[item_id] for item_id in candidate_ids]
        overrides = dict(answer_overrides or {})
        selected_by_link: dict[str, list[SemanticCandidate]] = {}
        for candidate in candidates:
            selected_by_link.setdefault(candidate.link_id, []).append(candidate)
        if any(
            link_id.startswith(DYNAMIC_LINK_PREFIX)
            or link_id not in selected_by_link
            or len(selected_by_link[link_id]) != 1
            for link_id in overrides
        ):
            raise ValueError("患者选择必须对应本轮唯一的受控问卷字段")
        selection_rows: list[dict[str, Any]] = []
        if overrides:
            task = self._task_for_record(record)
            questionnaire_items = {
                item.link_id: item for item in task.allowed_items
            }
            selected_candidates: list[SemanticCandidate] = []
            for candidate in candidates:
                if candidate.link_id not in overrides:
                    selected_candidates.append(candidate)
                    continue
                selected_answer = overrides[candidate.link_id]
                contract = questionnaire_items.get(candidate.link_id)
                allowed_answers = {
                    option.code for option in (contract.answer_options if contract else [])
                }
                if (
                    contract is None
                    or contract.item_type != "choice"
                    or selected_answer not in allowed_answers
                ):
                    raise ValueError("患者选择不属于锁定问卷的受控选项")
                effective = candidate.effective_time
                if effective is not None:
                    effective = effective.model_copy(
                        update={
                            "basis": TemporalResolutionBasis.PATIENT_CONFIRMATION,
                            "inherited_from_action_id": candidate.candidate_id,
                        }
                    )
                elif result.temporal_context is not None:
                    effective = candidate_temporal_resolution(
                        candidate,
                        result.temporal_context,
                        basis=TemporalResolutionBasis.PATIENT_CONFIRMATION,
                        inherited_from_action_id=candidate.candidate_id,
                    )
                selected_candidate = candidate.model_copy(
                    update={"answer": selected_answer, "effective_time": effective}
                )
                selected_candidates.append(selected_candidate)
                selection_rows.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "link_id": candidate.link_id,
                        "model_answer": candidate.answer,
                        "patient_selected_answer": selected_answer,
                    }
                )
            candidates = selected_candidates
        self._preflight_action_decisions(
            record,
            {
                candidate.candidate_id: ContextResolutionDecision.ACCEPTED
                for candidate in candidates
            },
        )
        resolved_at = utc_now_iso()
        plan = self.prepare_confirmed_candidates(
            run_id,
            candidate_ids if not overrides else [],
            resolved_at=resolved_at,
            include_original_text=include_original_text,
            track_original_text_context=track_original_text_context,
            _candidate_values=(candidates if overrides else None),
        )
        selection_events: list[AuditEvent] = []
        selection_option_id = None
        audit_decision = "accepted"
        if selection_rows:
            selection_payload = json.dumps(
                {
                    "session_id": record.session_id,
                    "source_run_id": record.run_id,
                    "selections": selection_rows,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            selection_digest = hashlib.sha256(
                selection_payload.encode("utf-8")
            ).hexdigest()
            selection_option_id = f"patient-selection:{selection_digest[:24]}"
            audit_decision = "accepted_with_patient_selection"
            selection_events.append(
                build_audit_event(
                    event_id=f"audit-{selection_digest[:32]}-selection",
                    patient_id=record.patient_id,
                    entity_type="CareSession",
                    entity_id=record.session_id,
                    event_type="patient_answer_selected",
                    actor_type="synthetic_patient",
                    created_at=resolved_at,
                    details={
                        "session_id": record.session_id,
                        "source_run_id": record.run_id,
                        "selections": selection_rows,
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        return self._persist_conversation_decision(
            plan=plan,
            action_ids=candidate_ids,
            decision="accepted",
            resolution_decision=ContextResolutionDecision.ACCEPTED,
            option_id=selection_option_id,
            audit_decision=audit_decision,
            additional_audit_events=selection_events,
        )

    def stage_candidates_for_final_review(
        self,
        run_id: str,
        candidate_ids: list[str],
        *,
        include_original_text: bool = True,
        track_original_text_context: bool = False,
    ) -> CareSession:
        """Place validated model output in an unconfirmed draft, never clinical data."""

        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(not item.strip() for item in candidate_ids)
        ):
            raise ValueError("待整理草稿必须包含本轮非空唯一候选")
        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        if set(candidate_ids) - set(available):
            raise ValueError("草稿内容不属于该次安全审核结果")
        plan = self.prepare_confirmed_candidates(
            run_id,
            candidate_ids,
            include_original_text=include_original_text,
            track_original_text_context=track_original_text_context,
            _provisional=True,
        )
        if not isinstance(plan, ProvisionalCandidatePlan):
            raise RuntimeError("provisional candidate preparation returned the wrong plan")
        return self._persist_provisional_plan(
            plan=plan,
            action_ids=candidate_ids,
            action_type="candidate",
            decision="drafted",
            option_id=None,
            persist_answers=True,
        )

    def resolve_clarification_for_final_review(
        self,
        run_id: str,
        clarification_id: str,
        option_id: str,
        *,
        include_original_text: bool = True,
        track_original_text_context: bool = False,
    ) -> CareSession:
        """Resolve ambiguity into a provisional draft, not a patient confirmation."""

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
        candidate = self.prepare_clarification_candidate(
            run_id,
            clarification_id,
            option_id,
            temporal_basis=TemporalResolutionBasis.PATIENT_SELECTION,
        )
        if candidate is None:
            decision = "unsure" if option_id == "unsure" else "rejected"
            session = self._session(record.session_id)
            plan = ProvisionalCandidatePlan(
                record=record,
                session=session,
                candidates=[],
                answers=session.answers,
                answer_contexts=[],
                symptom_reports=[],
                resolved_at=utc_now_iso(),
            )
            return self._persist_provisional_plan(
                plan=plan,
                action_ids=[clarification_id],
                action_type="clarification",
                decision=decision,
                option_id=option_id,
                persist_answers=False,
            )
        plan = self.prepare_confirmed_candidates(
            run_id,
            [],
            include_original_text=include_original_text,
            track_original_text_context=track_original_text_context,
            _candidate_values=[candidate],
            _provisional=True,
        )
        if not isinstance(plan, ProvisionalCandidatePlan):
            raise RuntimeError("provisional clarification returned the wrong plan")
        return self._persist_provisional_plan(
            plan=plan,
            action_ids=[clarification_id],
            action_type="clarification",
            decision="drafted",
            option_id=option_id,
            persist_answers=True,
        )

    def prepare_confirmed_candidates(
        self,
        run_id: str,
        candidate_ids: list[str],
        *,
        resolved_at: str | None = None,
        require_complete_set: bool = False,
        include_original_text: bool = True,
        track_original_text_context: bool = False,
        _candidate_values: list[SemanticCandidate] | None = None,
        _provisional: bool = False,
    ) -> ConfirmedCandidatePlan | ProvisionalCandidatePlan:
        """Validate and materialize a confirmation without changing the store."""

        if not candidate_ids and _candidate_values is None:
            raise ValueError("请选择至少一项记录后再确认")
        record, result = self._stored_result(run_id)
        if result.status == SemanticStatus.BLOCKED:
            raise ValueError("被安全边界阻断的内容不能确认")
        if require_complete_set and result.clarifications:
            raise ValueError("仍有澄清问题未处理，不能创建护士复核任务")
        if _candidate_values is None:
            available = {item.candidate_id: item for item in result.candidates}
            if set(candidate_ids) - set(available):
                raise ValueError("确认内容不属于该次安全审核结果")
            if require_complete_set and set(candidate_ids) != set(available):
                raise ValueError("必须一次处理本轮全部候选，不能留下未决候选")
            candidates = [available[item_id] for item_id in candidate_ids]
            self._preflight_action_decisions(
                record,
                {
                    candidate.candidate_id: ContextResolutionDecision.ACCEPTED
                    for candidate in candidates
                },
            )
        else:
            candidates = _candidate_values
        session = self._session(record.session_id)
        if session.patient_id != record.patient_id:
            raise ValueError("Agent 运行记录与随访会话患者不一致")
        questionnaire = self.care_engine.questionnaire_for_session(session)
        questionnaire_link_ids = {
            item["linkId"]
            for item in flatten_questionnaire_items(questionnaire.get("item", []))
            if item.get("type") not in {"display", "group"}
        }
        active_contexts = (
            self.store.list_active_provisional_answer_contexts(session.session_id)
            if _provisional
            else self.store.list_active_answer_contexts(session.session_id)
        )
        active_by_link: dict[str, Any] = {}
        for context in active_contexts:
            if context.link_id in active_by_link:
                raise ValueError("同一草稿字段存在多个当前来源，不能继续确认")
            active_by_link[context.link_id] = context
        answers: dict[str, Any] = dict(session.answers)
        original = record.input_text.strip()
        free_text_updated = False
        if original and include_original_text:
            previous = str(answers.get("free-text-report", "")).strip()
            lines = previous.splitlines() if previous else []
            if original not in lines:
                answers["free-text-report"] = "\n".join([*lines, original])
                free_text_updated = True

        temporal = result.temporal_context
        now = resolved_at or utc_now_iso()
        contexts: list[Any] = []
        reports: list[Any] = []
        corrections: list[DraftAnswerCorrection] = []
        invalidations: list[DraftAnswerInvalidation] = []
        if free_text_updated and track_original_text_context:
            context_kwargs = {
                "session_id": session.session_id,
                "link_id": "free-text-report",
                "answer": answers["free-text-report"],
                "source_run_id": record.run_id,
                "followup_occurrence_id": (
                        temporal.followup_occurrence_id
                        if temporal is not None
                        else f"occurrence-{session.session_id}"
                    ),
                "patient_timezone": (
                        temporal.patient_timezone
                        if temporal is not None
                        else self.patient_timezone
                    ),
                "reported_at": (
                        temporal.received_at_local
                        if temporal is not None
                        else record.completed_at
                    ),
                "temporal_kind": None,
                "resolution_basis": TemporalResolutionBasis.EXPLICIT_PATIENT_TEXT.value,
                "raw_text": original,
                "created_at": now,
            }
            if _provisional:
                contexts.append(
                    ProvisionalAnswerContext(
                        draft_context_id=(
                            "draft-context-"
                            + uuid5(
                                NAMESPACE_URL,
                                f"{record.run_id}|free-text-report|{answers['free-text-report']}",
                            ).hex
                        ),
                        **context_kwargs,
                    )
                )
            else:
                contexts.append(
                    ConfirmedAnswerContext(
                        answer_context_id=(
                            "answer-context-"
                            + uuid5(
                                NAMESPACE_URL,
                                f"{record.run_id}|free-text-report|{answers['free-text-report']}",
                            ).hex
                        ),
                        **context_kwargs,
                    )
                )
        for candidate in candidates:
            effective = candidate.effective_time
            if effective is None and temporal is not None:
                effective = candidate_temporal_resolution(candidate, temporal)
            terminology_match = (
                candidate.terminology_match.model_dump(mode="json")
                if candidate.terminology_match is not None
                else None
            )
            if candidate.link_id.startswith(DYNAMIC_LINK_PREFIX):
                if candidate.terminology_match is None:
                    raise ValueError("患者自述症状缺少经过校验的术语匹配")
                match = candidate.terminology_match
                report_kwargs = {
                    "session_id": session.session_id,
                    "concept_id": match.concept_id,
                    "preferred_zh": match.preferred_zh,
                    "coding": match.coding.model_dump(mode="json"),
                    "terminology_match": terminology_match,
                    "source_kind": candidate.origin.value,
                    "source_run_id": record.run_id,
                    "evidence_text": candidate.evidence_text,
                    "evidence_start": candidate.evidence_start,
                    "evidence_end": candidate.evidence_end,
                    "followup_occurrence_id": (
                            temporal.followup_occurrence_id
                            if temporal is not None
                            else f"occurrence-{session.session_id}"
                        ),
                    "patient_timezone": (
                            temporal.patient_timezone
                            if temporal is not None
                            else self.patient_timezone
                        ),
                    "reported_at": (
                            temporal.received_at_local
                            if temporal is not None
                            else record.completed_at
                        ),
                    "effective_start": (
                            effective.effective_start if effective is not None else None
                        ),
                    "effective_end": (
                            effective.effective_end if effective is not None else None
                        ),
                    "temporal_kind": (
                            effective.kind.value if effective is not None else None
                        ),
                    "created_at": now,
                }
                report_identity = uuid5(
                    NAMESPACE_URL,
                    f"{record.run_id}|{candidate.candidate_id}",
                ).hex
                if _provisional:
                    reports.append(
                        ProvisionalSymptomReport(
                            draft_report_id=f"draft-symptom-report-{report_identity}",
                            **report_kwargs,
                        )
                    )
                else:
                    reports.append(
                        ConfirmedSymptomReport(
                            report_id=f"symptom-report-{report_identity}",
                            **report_kwargs,
                        )
                    )
                continue
            previous_answer = session.answers.get(candidate.link_id)
            if (
                candidate.link_id in session.answers
                and previous_answer != candidate.answer
            ):
                previous_context = active_by_link.get(candidate.link_id)
                if (
                    previous_context is None
                    or previous_context.answer != previous_answer
                ):
                    raise ValueError("待修改字段缺少与当前值一致的上一版来源")
                correction_payload = json.dumps(
                    {
                        "link_id": candidate.link_id,
                        "previous_context_id": (
                            previous_context.draft_context_id
                            if _provisional
                            else previous_context.answer_context_id
                        ),
                        "replacement": candidate.answer,
                        "source_run_id": record.run_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                corrections.append(
                    DraftAnswerCorrection(
                        correction_id=(
                            "correction-"
                            + uuid5(NAMESPACE_URL, correction_payload).hex
                        ),
                        link_id=candidate.link_id,
                        previous_answer=previous_answer,
                        replacement_answer=candidate.answer,
                        previous_source_run_id=previous_context.source_run_id,
                        correction_source_run_id=record.run_id,
                        raw_text=record.input_text,
                    )
                )
            answers[candidate.link_id] = candidate.answer
            context_kwargs = {
                "session_id": session.session_id,
                "link_id": candidate.link_id,
                "answer": candidate.answer,
                "source_run_id": record.run_id,
                "followup_occurrence_id": (
                        temporal.followup_occurrence_id
                        if temporal is not None
                        else f"occurrence-{session.session_id}"
                    ),
                "patient_timezone": (
                        temporal.patient_timezone
                        if temporal is not None
                        else self.patient_timezone
                    ),
                "reported_at": (
                        temporal.received_at_local
                        if temporal is not None
                        else record.completed_at
                    ),
                "effective_start": (
                        effective.effective_start if effective is not None else None
                    ),
                "effective_end": (
                        effective.effective_end if effective is not None else None
                    ),
                "temporal_kind": (
                        effective.kind.value if effective is not None else None
                    ),
                "resolution_basis": (
                        effective.basis.value if effective is not None else None
                    ),
                "raw_text": record.input_text,
                "terminology_match": terminology_match,
                "created_at": now,
            }
            context_identity = uuid5(
                NAMESPACE_URL,
                f"{record.run_id}|{candidate.candidate_id}|{candidate.link_id}",
            ).hex
            if _provisional:
                contexts.append(
                    ProvisionalAnswerContext(
                        draft_context_id=f"draft-context-{context_identity}",
                        **context_kwargs,
                    )
                )
            else:
                contexts.append(
                    ConfirmedAnswerContext(
                        answer_context_id=f"answer-context-{context_identity}",
                        **context_kwargs,
                    )
                )
        structured_candidate_links = [
            item.link_id
            for item in candidates
            if not item.link_id.startswith(DYNAMIC_LINK_PREFIX)
        ]
        if len(structured_candidate_links) != len(set(structured_candidate_links)):
            raise ValueError("同一轮不能为一个问卷字段确认多个不同候选")

        visible_before = {
            item["linkId"]
            for item in visible_questionnaire_items(questionnaire, session.answers)
        }
        preexisting_disabled = {
            link_id
            for link_id in session.answers
            if link_id in questionnaire_link_ids and link_id not in visible_before
        }
        if preexisting_disabled:
            raise ValueError("当前草稿包含已失效的条件字段，不能继续修改")
        visible_after = {
            item["linkId"] for item in visible_questionnaire_items(questionnaire, answers)
        }
        newly_disabled_candidates = {
            link_id
            for link_id in structured_candidate_links
            if link_id in questionnaire_link_ids and link_id not in visible_after
        }
        if newly_disabled_candidates:
            raise ValueError("本轮候选之间违反 Questionnaire 条件依赖")
        invalidated_links = sorted(
            link_id
            for link_id in session.answers
            if link_id in questionnaire_link_ids and link_id not in visible_after
        )
        changed_links = sorted(
            {
                item.link_id
                for item in candidates
                if not item.link_id.startswith(DYNAMIC_LINK_PREFIX)
                and session.answers.get(item.link_id) != item.answer
            }
        )
        for link_id in invalidated_links:
            previous_context = active_by_link.get(link_id)
            if (
                previous_context is None
                or previous_context.answer != session.answers.get(link_id)
            ):
                raise ValueError("失效的条件字段缺少可验证的上一版来源")
            invalidations.append(
                DraftAnswerInvalidation(
                    link_id=link_id,
                    previous_answer=session.answers[link_id],
                    previous_source_run_id=previous_context.source_run_id,
                    invalidated_by_link_ids=changed_links,
                )
            )
            answers.pop(link_id, None)

        # Validate the exact post-confirmation draft before any persistence.  This
        # is also the gate that prevents disabled Questionnaire items surviving a
        # parent-answer correction.
        build_questionnaire_response(
            questionnaire=questionnaire,
            response_id=f"draft-{session.session_id.removeprefix('session-')}",
            patient_id=session.patient_id,
            authored=now,
            answers=answers,
            status="in-progress",
        )
        plan_type = ProvisionalCandidatePlan if _provisional else ConfirmedCandidatePlan
        return plan_type(
            record=record,
            session=session,
            candidates=candidates,
            answers=answers,
            answer_contexts=contexts,
            symptom_reports=reports,
            corrections=corrections,
            invalidations=invalidations,
            resolved_at=now,
        )

    def reject_candidates(self, run_id: str, candidate_ids: list[str]) -> None:
        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(not item.strip() for item in candidate_ids)
            or set(candidate_ids) - set(available)
        ):
            raise ValueError("拒绝内容必须是该次安全审核中的非空唯一候选集")
        candidates = [available[item_id] for item_id in candidate_ids]
        self._preflight_action_decisions(
            record,
            {
                candidate.candidate_id: ContextResolutionDecision.REJECTED
                for candidate in candidates
            },
        )
        session = self._session(record.session_id)
        resolved_at = utc_now_iso()
        self._persist_conversation_decision(
            plan=ConfirmedCandidatePlan(
                record=record,
                session=session,
                candidates=candidates,
                answers=session.answers,
                answer_contexts=[],
                symptom_reports=[],
                resolved_at=resolved_at,
            ),
            action_ids=candidate_ids,
            decision="rejected",
            resolution_decision=ContextResolutionDecision.REJECTED,
            option_id=None,
            audit_decision="rejected",
            persist_answers=False,
        )

    def mark_candidates_unsure(self, run_id: str, candidate_ids: list[str]) -> None:
        """Record uncertainty without releasing any draft or clinical resource."""

        record, result = self._stored_result(run_id)
        available = {item.candidate_id: item for item in result.candidates}
        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or any(not item.strip() for item in candidate_ids)
            or set(candidate_ids) - set(available)
        ):
            raise ValueError("不确定内容不属于该次安全审核结果")
        candidates = [available[item_id] for item_id in candidate_ids]
        self._preflight_action_decisions(
            record,
            {
                item.candidate_id: ContextResolutionDecision.UNSURE
                for item in candidates
            },
        )
        session = self._session(record.session_id)
        self._persist_conversation_decision(
            plan=ConfirmedCandidatePlan(
                record=record,
                session=session,
                candidates=candidates,
                answers=session.answers,
                answer_contexts=[],
                symptom_reports=[],
                resolved_at=utc_now_iso(),
            ),
            action_ids=candidate_ids,
            decision="unsure",
            resolution_decision=ContextResolutionDecision.UNSURE,
            option_id=None,
            audit_decision="unsure",
            persist_answers=False,
        )

    def confirm_original_text(self, run_id: str):
        """Patient explicitly chooses to retain only their verbatim report."""

        record, result = self._stored_result(run_id)
        if result.status.value == "blocked":
            raise ValueError("指令型文本不能作为患者健康原话保存")
        action_ids = [
            *[item.candidate_id for item in result.candidates],
            *[item.clarification_id for item in result.clarifications],
        ]
        self._preflight_action_decisions(
            record,
            {
                action_id: ContextResolutionDecision.REJECTED
                for action_id in action_ids
            },
        )
        resolved_at = utc_now_iso()
        plan = self.prepare_confirmed_candidates(
            run_id,
            [],
            resolved_at=resolved_at,
            _candidate_values=[],
        )
        return self._persist_conversation_decision(
            plan=plan,
            action_ids=action_ids,
            decision="verbatim-only",
            resolution_decision=ContextResolutionDecision.REJECTED,
            option_id=None,
            audit_decision="verbatim_only_accepted",
        )

    def prepare_clarification_candidate(
        self,
        run_id: str,
        clarification_id: str,
        option_id: str,
        *,
        temporal_basis: TemporalResolutionBasis = TemporalResolutionBasis.PATIENT_CONFIRMATION,
    ) -> SemanticCandidate | None:
        """Materialize one explicit option without mutating session state."""

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
        task = self._task_for_record(record)
        if clarification.kind == ClarificationKind.TERMINOLOGY_DISAMBIGUATION:
            mention = clarification.reported_symptom
            if option.terminology_match is None or mention is None:
                return None
            candidate = self._symptom_candidate(
                task,
                mention,
                option.terminology_match,
                force_current=mention.temporality == Temporality.UNSPECIFIED,
            )
            candidate = candidate.model_copy(
                update={
                    "effective_time": candidate_temporal_resolution(
                        candidate,
                        task.temporal_context,
                        basis=temporal_basis,
                        inherited_from_action_id=clarification.clarification_id,
                    )
                }
            )
        else:
            candidate = clarification.proposed_candidate
            if not option.accepts_candidate or candidate is None:
                return None
            if clarification.kind == ClarificationKind.CONFIRM_TIME_WINDOW:
                candidate = candidate.model_copy(
                    update={"temporality": Temporality.EXPLICIT_24H}
                )
            elif clarification.kind == ClarificationKind.CONFIRM_CURRENT:
                candidate = candidate.model_copy(
                    update={"temporality": Temporality.CURRENT}
                )
            candidate = candidate.model_copy(
                update={
                    "effective_time": candidate_temporal_resolution(
                        candidate,
                        task.temporal_context,
                        basis=temporal_basis,
                        inherited_from_action_id=clarification.clarification_id,
                    )
                }
            )
        if self.safety.review_candidate(task, candidate):
            raise ValueError("澄清后的候选未通过安全校验")
        return candidate

    def resolve_clarification(
        self,
        run_id: str,
        clarification_id: str,
        option_id: str,
        *,
        include_original_text: bool = True,
        track_original_text_context: bool = False,
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
        if clarification.kind == ClarificationKind.TERMINOLOGY_DISAMBIGUATION:
            decision = (
                ContextResolutionDecision.UNSURE
                if option.terminology_match is None
                or clarification.reported_symptom is None
                else ContextResolutionDecision.ACCEPTED
            )
        elif not option.accepts_candidate or candidate is None:
            decision = (
                ContextResolutionDecision.UNSURE
                if option_id == "unsure"
                else ContextResolutionDecision.REJECTED
            )
        else:
            decision = ContextResolutionDecision.ACCEPTED
        self._preflight_action_decisions(
            record,
            {clarification.clarification_id: decision},
        )
        resolved_at = utc_now_iso()
        if clarification.kind == ClarificationKind.TERMINOLOGY_DISAMBIGUATION:
            mention = clarification.reported_symptom
            if option.terminology_match is None or mention is None:
                session = self._session(record.session_id)
                return self._persist_conversation_decision(
                    plan=ConfirmedCandidatePlan(
                        record=record,
                        session=session,
                        candidates=[],
                        answers=session.answers,
                        answer_contexts=[],
                        symptom_reports=[],
                        resolved_at=resolved_at,
                    ),
                    action_ids=[clarification.clarification_id],
                    decision=decision.value,
                    resolution_decision=decision,
                    option_id=option_id,
                    audit_decision=f"terminology_{option_id}",
                    persist_answers=False,
                )
            task = self._task_for_record(record)
            candidate = self._symptom_candidate(
                task,
                mention,
                option.terminology_match,
                force_current=mention.temporality == Temporality.UNSPECIFIED,
            )
            candidate = candidate.model_copy(
                update={
                    "effective_time": candidate_temporal_resolution(
                        candidate,
                        task.temporal_context,
                        basis=TemporalResolutionBasis.PATIENT_CONFIRMATION,
                        inherited_from_action_id=clarification.clarification_id,
                    )
                }
            )
            errors = self.safety.review_candidate(task, candidate)
            if errors:
                raise ValueError("术语澄清后的候选未通过安全校验")
            plan = self.prepare_confirmed_candidates(
                run_id,
                [],
                resolved_at=resolved_at,
                include_original_text=include_original_text,
                track_original_text_context=track_original_text_context,
                _candidate_values=[candidate],
            )
            return self._persist_conversation_decision(
                plan=plan,
                action_ids=[clarification.clarification_id],
                decision=ContextResolutionDecision.ACCEPTED.value,
                resolution_decision=ContextResolutionDecision.ACCEPTED,
                option_id=option_id,
                audit_decision="terminology_clarification_accepted",
            )
        if not option.accepts_candidate or candidate is None:
            session = self._session(record.session_id)
            return self._persist_conversation_decision(
                plan=ConfirmedCandidatePlan(
                    record=record,
                    session=session,
                    candidates=[candidate] if candidate else [],
                    answers=session.answers,
                    answer_contexts=[],
                    symptom_reports=[],
                    resolved_at=resolved_at,
                ),
                action_ids=[clarification.clarification_id],
                decision=decision.value,
                resolution_decision=decision,
                option_id=option_id,
                audit_decision=f"clarification_{option_id}",
                persist_answers=False,
            )

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
        plan = self.prepare_confirmed_candidates(
            run_id,
            [],
            resolved_at=resolved_at,
            include_original_text=include_original_text,
            track_original_text_context=track_original_text_context,
            _candidate_values=[candidate],
        )
        return self._persist_conversation_decision(
            plan=plan,
            action_ids=[clarification.clarification_id],
            decision=ContextResolutionDecision.ACCEPTED.value,
            resolution_decision=ContextResolutionDecision.ACCEPTED,
            option_id=option_id,
            audit_decision="clarification_accepted",
        )

    def _preflight_action_decisions(
        self,
        record: AgentRunRecord,
        decisions: dict[str, ContextResolutionDecision],
    ) -> None:
        existing = self.store.conversation_action_decisions(record.session_id)
        for action_id, decision in decisions.items():
            current = existing.get(action_id)
            if current in {
                ContextResolutionDecision.ACCEPTED.value,
                ContextResolutionDecision.REJECTED.value,
            } and current != decision.value:
                raise ValueError("该对话操作已经完成，不能重复提交")

    def _persist_provisional_plan(
        self,
        *,
        plan: ProvisionalCandidatePlan,
        action_ids: list[str],
        action_type: str,
        decision: str,
        option_id: str | None,
        persist_answers: bool,
    ) -> CareSession:
        """Persist one provisional turn with no patient-confirmed side effects."""

        if persist_answers:
            questionnaire = self.care_engine.questionnaire_for_session(plan.session)
            build_questionnaire_response(
                questionnaire=questionnaire,
                response_id=f"draft-{plan.session.session_id.removeprefix('session-')}",
                patient_id=plan.session.patient_id,
                authored=plan.resolved_at,
                answers=plan.answers,
                status="in-progress",
            )
        identity_payload = json.dumps(
            {
                "source_run_id": plan.record.run_id,
                "action_ids": sorted(action_ids),
                "action_type": action_type,
                "decision": decision,
                "option_id": option_id,
                "confirmation_status": "pending_final_review",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        identity = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
        context_digests = {
            item.draft_context_id: _provisional_material_sha256(item)
            for item in plan.answer_contexts
        }
        report_digests = {
            item.draft_report_id: _provisional_material_sha256(item)
            for item in plan.symptom_reports
        }
        audits: list[AuditEvent] = []
        if persist_answers:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{identity[:32]}-provisional",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="care_session_provisional_draft_saved",
                    actor_type="deterministic_workflow",
                    created_at=plan.resolved_at,
                    details={
                        "session_id": plan.record.session_id,
                        "source_run_id": plan.record.run_id,
                        "action_ids": sorted(action_ids),
                        "draft_context_ids": sorted(
                            item.draft_context_id for item in plan.answer_contexts
                        ),
                        "draft_report_ids": sorted(
                            item.draft_report_id for item in plan.symptom_reports
                        ),
                        "draft_context_sha256": context_digests,
                        "draft_report_sha256": report_digests,
                        "answered_link_ids": sorted(plan.answers),
                        "confirmation_status": "pending_final_review",
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        audits.append(
            build_audit_event(
                event_id=f"audit-{identity[:32]}-draft-action",
                patient_id=plan.record.patient_id,
                entity_type="AgentRun",
                entity_id=plan.record.run_id,
                event_type="semantic_candidate_staged_to_draft",
                actor_type="deterministic_workflow",
                created_at=plan.resolved_at,
                details={
                    "session_id": plan.record.session_id,
                    "action_ids": sorted(action_ids),
                    "action_type": action_type,
                    "decision": decision,
                    "option_id": option_id,
                    "staged_link_ids": sorted(
                        item.link_id for item in plan.candidates
                    ),
                    "confirmation_status": "pending_final_review",
                    "clinical_assessment": "not_assessed",
                },
            )
        )
        for correction in plan.corrections:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{correction.correction_id}-draft",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="patient_draft_answer_revised",
                    actor_type="synthetic_patient",
                    created_at=plan.resolved_at,
                    details={
                        "session_id": plan.record.session_id,
                        "link_id": correction.link_id,
                        "previous_answer": correction.previous_answer,
                        "replacement_answer": correction.replacement_answer,
                        "previous_source_run_id": correction.previous_source_run_id,
                        "correction_source_run_id": correction.correction_source_run_id,
                        "raw_text": correction.raw_text,
                        "confirmation_status": "pending_final_review",
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        if plan.invalidations:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{identity[:32]}-draft-dependency",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="patient_draft_dependencies_invalidated",
                    actor_type="deterministic_questionnaire_policy",
                    created_at=plan.resolved_at,
                    details={
                        "session_id": plan.record.session_id,
                        "invalidations": [
                            item.model_dump(mode="json") for item in plan.invalidations
                        ],
                        "correction_source_run_id": plan.record.run_id,
                        "confirmation_status": "pending_final_review",
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        self.store.persist_provisional_draft_bundle(
            expected_session=plan.session,
            answers=plan.answers if persist_answers else None,
            answer_contexts=plan.answer_contexts if persist_answers else [],
            symptom_reports=plan.symptom_reports if persist_answers else [],
            supersede_link_ids=(
                [item.link_id for item in plan.invalidations]
                if persist_answers
                else []
            ),
            action_ids=action_ids,
            action_type=action_type,
            source_run_id=plan.record.run_id,
            decision=decision,
            option_id=option_id,
            resolved_at=plan.resolved_at,
            audit_events=audits,
        )
        session = self.store.get_care_session(plan.record.session_id)
        if session is None:
            raise ValueError("随访会话不存在")
        return session

    def _persist_conversation_decision(
        self,
        *,
        plan: ConfirmedCandidatePlan,
        action_ids: list[str],
        decision: str,
        resolution_decision: ContextResolutionDecision,
        option_id: str | None,
        audit_decision: str,
        persist_answers: bool = True,
        response_run_id: str | None = None,
        response_text: str | None = None,
        response_record: AgentRunRecord | None = None,
        additional_audit_events: list[AuditEvent] | None = None,
        context_audit_details: dict[str, Any] | None = None,
    ) -> CareSession:
        """Validate a draft and hand one immutable decision bundle to SQLite."""

        if persist_answers:
            questionnaire = self.care_engine.questionnaire_for_session(plan.session)
            build_questionnaire_response(
                questionnaire=questionnaire,
                response_id=f"draft-{plan.session.session_id.removeprefix('session-')}",
                patient_id=plan.session.patient_id,
                authored=plan.resolved_at,
                answers=plan.answers,
                status="in-progress",
            )
        identity_payload = json.dumps(
            {
                "source_run_id": plan.record.run_id,
                "action_ids": sorted(action_ids),
                "decision": decision,
                "option_id": option_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
        audits = list(additional_audit_events or [])
        if persist_answers:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{identity[:32]}-draft",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="care_session_draft_saved",
                    actor_type="synthetic_patient",
                    created_at=plan.resolved_at,
                    details={
                        "answered_link_ids": sorted(plan.answers),
                        "correction_ids": [
                            item.correction_id for item in plan.corrections
                        ],
                        "invalidated_link_ids": [
                            item.link_id for item in plan.invalidations
                        ],
                    },
                )
            )
        audits.append(
            build_audit_event(
                event_id=f"audit-{identity[:32]}-decision",
                patient_id=plan.record.patient_id,
                entity_type="AgentRun",
                entity_id=plan.record.run_id,
                event_type="semantic_candidate_patient_decision",
                actor_type="synthetic_patient",
                created_at=plan.resolved_at,
                details={
                    "session_id": plan.record.session_id,
                    "decision": audit_decision,
                    "candidate_ids": [item.candidate_id for item in plan.candidates],
                    "confirmed_link_ids": [item.link_id for item in plan.candidates],
                    "correction_ids": [
                        item.correction_id for item in plan.corrections
                    ],
                    "invalidated_link_ids": [
                        item.link_id for item in plan.invalidations
                    ],
                    "clinical_assessment": "not_assessed",
                },
            )
        )
        for correction in plan.corrections:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{correction.correction_id}",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="patient_answer_corrected",
                    actor_type="synthetic_patient",
                    created_at=plan.resolved_at,
                    details={
                        "correction_id": correction.correction_id,
                        "session_id": plan.record.session_id,
                        "link_id": correction.link_id,
                        "previous_answer": correction.previous_answer,
                        "replacement_answer": correction.replacement_answer,
                        "previous_source_run_id": correction.previous_source_run_id,
                        "correction_source_run_id": (
                            correction.correction_source_run_id
                        ),
                        "raw_text": correction.raw_text,
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        if plan.invalidations:
            invalidation_payload = json.dumps(
                [item.model_dump(mode="json") for item in plan.invalidations],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            invalidation_id = hashlib.sha256(
                f"{plan.record.run_id}|{invalidation_payload}".encode("utf-8")
            ).hexdigest()[:32]
            audits.append(
                build_audit_event(
                    event_id=f"audit-{invalidation_id}-dependency",
                    patient_id=plan.record.patient_id,
                    entity_type="CareSession",
                    entity_id=plan.record.session_id,
                    event_type="patient_answers_dependency_invalidated",
                    actor_type="deterministic_questionnaire_policy",
                    created_at=plan.resolved_at,
                    details={
                        "session_id": plan.record.session_id,
                        "invalidations": [
                            item.model_dump(mode="json")
                            for item in plan.invalidations
                        ],
                        "correction_source_run_id": plan.record.run_id,
                        "clinical_assessment": "not_assessed",
                    },
                )
            )
        if context_audit_details is not None:
            audits.append(
                build_audit_event(
                    event_id=f"audit-{identity[:32]}-context",
                    patient_id=plan.record.patient_id,
                    entity_type="AgentRun",
                    entity_id=response_run_id or plan.record.run_id,
                    event_type="conversation_context_resolved",
                    actor_type="deterministic_context_resolver",
                    created_at=plan.resolved_at,
                    details=context_audit_details,
                )
            )
        self.store.persist_conversation_decision_bundle(
            expected_session=plan.session,
            answers=plan.answers if persist_answers else None,
            answer_contexts=plan.answer_contexts if persist_answers else [],
            symptom_reports=plan.symptom_reports if persist_answers else [],
            supersede_link_ids=(
                [item.link_id for item in plan.invalidations]
                if persist_answers
                else []
            ),
            action_ids=action_ids,
            source_run_id=plan.record.run_id,
            decision=decision,
            resolution_decision=resolution_decision.value,
            option_id=option_id,
            response_run_id=response_run_id,
            response_text=response_text,
            resolved_at=plan.resolved_at,
            audit_events=audits,
            response_record=response_record,
        )
        session = self.store.get_care_session(plan.record.session_id)
        if session is None:
            raise ValueError("随访会话不存在")
        return session

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
            knowledge_release_id=task.knowledge_release_id,
            terminology_catalog_id=task.terminology_catalog_id,
            terminology_catalog_version=task.terminology_catalog_version,
            terminology_catalog_sha256=task.terminology_catalog_sha256,
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
        self._task_for_record(record)
        return record, SemanticResult.model_validate(record.output_json)

    def _task_for_record(self, record: AgentRunRecord) -> SemanticTask:
        stored_session = self.store.get_care_session(record.session_id)
        allow_completed_anchor = bool(
            record.task_id.startswith("supplemental:")
            and stored_session is not None
            and stored_session.parent_session_id is None
        )
        session = self._session(
            record.session_id,
            allow_completed_anchor=allow_completed_anchor,
        )
        questionnaire = self.care_engine.questionnaire_for_session(session)
        result = SemanticResult.model_validate(record.output_json)
        task = self._build_task(
            session,
            questionnaire,
            record.input_text,
            task_id=record.task_id,
            received_at=(
                result.temporal_context.received_at_utc
                if result.temporal_context is not None
                else record.started_at
            ),
        )
        self._require_record_boundary(record, task)
        return task

    @staticmethod
    def _record_matches_task(record: AgentRunRecord, task: SemanticTask) -> bool:
        return (
            record.knowledge_release_id == task.knowledge_release_id
            and record.terminology_catalog_id == task.terminology_catalog_id
            and record.terminology_catalog_version == task.terminology_catalog_version
            and record.terminology_catalog_sha256 == task.terminology_catalog_sha256
        )

    @classmethod
    def _require_record_boundary(
        cls, record: AgentRunRecord, task: SemanticTask
    ) -> None:
        if not cls._record_matches_task(record, task):
            raise ValueError("AgentRun knowledge or terminology release mismatch")

    def _session(self, session_id: str, *, allow_completed_anchor: bool = False):
        session = self.store.get_care_session(session_id)
        if session is None:
            raise ValueError("随访会话不存在")
        allowed_statuses = (
            {"in_progress", "completed"}
            if allow_completed_anchor
            else {"in_progress"}
        )
        if session.status.value not in allowed_statuses:
            raise ValueError("只有进行中的随访可以使用对话辅助填写")
        if allow_completed_anchor and session.status.value != "completed":
            raise ValueError("补充上报只能附加到已完成的当日随访")
        return session

    def _build_task(
        self,
        session,
        questionnaire: dict[str, Any],
        message_text: str,
        *,
        task_id: str | None = None,
        received_at: str | None = None,
    ) -> SemanticTask:
        message_text = message_text.strip()
        received_at = received_at or utc_now_iso()
        temporal_context = build_temporal_context(
            session_id=session.session_id,
            session_created_at=session.created_at,
            message_text=message_text,
            patient_timezone=self.patient_timezone,
            received_at=received_at,
        )
        conversation_context = self._conversation_context(
            session.session_id,
            temporal_context.followup_occurrence_id,
        )
        digest = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
        context_identity = json.dumps(
            {
                "knowledge_release_id": session.knowledge_release_id,
                "terminology_catalog_id": self.terminology.catalog.catalog_id,
                "terminology_catalog_version": self.terminology.catalog.version,
                "terminology_catalog_sha256": terminology_catalog_sha256(
                    self.terminology.catalog
                ),
                "local_date": temporal_context.local_date,
                "latest_run_id": (
                    conversation_context.recent_turns[-1].run_id
                    if conversation_context.recent_turns
                    else None
                ),
                "pending_action_ids": [
                    item.action_id for item in conversation_context.pending_actions
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        task_id = task_id or (
            "semantic-"
            + uuid5(
                NAMESPACE_URL,
                f"{session.session_id}|{digest}|{context_identity}",
            ).hex
        )
        admitted_responses = self.store.list_completed_questionnaire_responses(
            session.patient_id,
            pathway_code=session.pathway_code,
            pathway_version=session.pathway_version,
        )
        admitted_response_refs = {
            f"QuestionnaireResponse/{item['id']}" for item in admitted_responses
        }
        long_term_observations = self.store.list_final_observations(
            session.patient_id,
            pathway_code=session.pathway_code,
            pathway_version=session.pathway_version,
        )
        for item in long_term_observations:
            if item.as_fhir().get("status") != "final":
                raise ValueError("Layer 3 history only accepts final Observation")
            derived_responses = [
                reference
                for reference in (
                    entry.get("reference")
                    for entry in item.as_fhir().get("derivedFrom", [])
                )
                if isinstance(reference, str)
                and reference.startswith("QuestionnaireResponse/")
            ]
            if (
                len(derived_responses) != 1
                or derived_responses[0] not in admitted_response_refs
            ):
                raise ValueError(
                    "Layer 3 history contains an Observation outside the session Pathway"
                )
        long_term_observations = long_term_observations[:50]
        return SemanticTask(
            task_id=task_id,
            patient_id=session.patient_id,
            session_id=session.session_id,
            pathway_code=session.pathway_code,
            pathway_version=session.pathway_version,
            questionnaire_canonical=session.questionnaire_canonical,
            questionnaire_version=session.questionnaire_version,
            knowledge_release_id=session.knowledge_release_id,
            terminology_catalog_id=self.terminology.catalog.catalog_id,
            terminology_catalog_version=self.terminology.catalog.version,
            terminology_catalog_sha256=terminology_catalog_sha256(
                self.terminology.catalog
            ),
            message_text=message_text,
            existing_answers=session.answers,
            conversation_context=conversation_context,
            long_term_memory=[
                LongTermMemoryItem(
                    observation_id=item.observation_id,
                    code_system=item.code_system,
                    code=item.code,
                    display=item.code_display,
                    value_display=item.value_display,
                    effective_time=item.effective_time,
                    source_kind=item.evidence.source_kind,
                )
                for item in long_term_observations
            ],
            temporal_context=temporal_context,
            allowed_items=[
                *_questionnaire_contracts(questionnaire),
                *self.terminology.catalog.questionnaire_contracts(),
            ],
            created_at=temporal_context.received_at_utc,
        )

    def _conversation_context(
        self, session_id: str, followup_occurrence_id: str
    ) -> ConversationContext:
        # Short-term memory is the current daily follow-up occurrence, not an
        # arbitrary five-turn window.  A defensive cap only limits prompt size.
        max_turns = 100
        newest = []
        for record in self.store.list_agent_runs(session_id):
            result = SemanticResult.model_validate(record.output_json)
            temporal = result.temporal_context
            if (
                temporal is None
                or temporal.followup_occurrence_id == followup_occurrence_id
            ):
                newest.append(record)
            if len(newest) >= max_turns:
                break
        resolved = self.store.resolved_conversation_action_ids(session_id)
        recent_turns = []
        for record in reversed(newest):
            result = SemanticResult.model_validate(record.output_json)
            recent_turns.append(
                ConversationTurnContext(
                    run_id=record.run_id,
                    message_text=record.input_text,
                    status=result.status,
                    completed_at=record.completed_at,
                    candidate_link_ids=[
                        item.link_id for item in result.candidates
                    ],
                )
            )

        pending: list[PendingActionContext] = []
        for record in newest:
            result = SemanticResult.model_validate(record.output_json)
            run_actions = [
                PendingActionContext(
                    action_id=item.candidate_id,
                    action_type=PendingActionType.CANDIDATE_CONFIRMATION,
                    source_run_id=record.run_id,
                    link_id=item.link_id,
                    prompt=item.patient_message,
                    proposed_answer=item.answer,
                )
                for item in result.candidates
                if item.candidate_id not in resolved
            ]
            run_actions.extend(
                PendingActionContext(
                    action_id=item.clarification_id,
                    action_type=PendingActionType.CLARIFICATION,
                    source_run_id=record.run_id,
                    link_id=(
                        item.proposed_candidate.link_id
                        if item.proposed_candidate is not None
                        else item.target_link_id
                    ),
                    kind=item.kind,
                    prompt=item.prompt,
                    option_ids=[option.option_id for option in item.options],
                    proposed_answer=(
                        item.proposed_candidate.answer
                        if item.proposed_candidate is not None
                        else None
                    ),
                )
                for item in result.clarifications
                if item.clarification_id not in resolved
            )
            if run_actions:
                pending = run_actions
                break
        return ConversationContext(
            recent_turns=recent_turns,
            pending_actions=pending,
            memory_scope="daily_followup_session",
            followup_occurrence_id=followup_occurrence_id,
            max_turns=max_turns,
        )


def _provisional_material_sha256(item: Any) -> str:
    payload = json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _elapsed_ms(started_at: str, completed_at: str) -> int:
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
        return max(0, round((completed - started).total_seconds() * 1000))
    except ValueError:
        return 0


def _aggregate_stage_usage(
    traces: list[AgentStageTrace],
) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for trace in traces:
        for key, value in (trace.model_usage or {}).items():
            totals[key] = totals.get(key, 0) + value
    return totals or None


def _mention_temporality(
    task: SemanticTask, start: int, end: int
) -> Temporality:
    mentions = task.temporal_context.detected_mentions
    overlapping = [
        item
        for item in mentions
        if item.evidence_start < end and item.evidence_end > start
    ]
    considered = overlapping or mentions
    if any(
        item.kind.value in {"point_in_time", "partial_local_day"}
        for item in considered
    ):
        return Temporality.CURRENT
    if any(item.kind.value == "rolling_24h" for item in considered):
        return Temporality.EXPLICIT_24H
    if any(item.kind.value == "local_calendar_day" for item in considered):
        return Temporality.HISTORICAL
    return Temporality.UNSPECIFIED


def _mention_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 6) : start]
    return bool(re.search(r"(?:没有|没|未|无|不再|并无)\s*$", prefix))


_AFFIRMATIVE_REPLY = re.compile(
    r"^(?:是(?:的)?|对(?:的)?|没错|嗯+|好(?:的)?|确定|确认)[。！！!,.，\s]*$"
)
_NEGATIVE_REPLY = re.compile(
    r"^(?:不是|不对|没有|否|不确认)[。！！!,.，\s]*$"
)
_UNSURE_REPLY = re.compile(
    r"^(?:不确定|不知道|不清楚|记不清|想不起来)[。！！!,.，\s]*$"
)
_ALL_ACCEPTED_REPLY = re.compile(
    r"^(?:都对|都正确|全部正确|全部都对|都是的)[。！！!,.，\s]*$"
)


def _all_actions_reply(message_text: str) -> bool:
    return bool(_ALL_ACCEPTED_REPLY.fullmatch(message_text.strip()))


def _short_reply_decision(
    message_text: str, actions: list[PendingActionContext]
) -> ContextResolutionDecision | None:
    text_value = message_text.strip()
    if _all_actions_reply(text_value):
        if all(
            item.action_type == PendingActionType.CANDIDATE_CONFIRMATION
            for item in actions
        ):
            return ContextResolutionDecision.ACCEPTED
        return None
    if len(actions) != 1:
        return None
    if _AFFIRMATIVE_REPLY.fullmatch(text_value):
        return ContextResolutionDecision.ACCEPTED
    if _NEGATIVE_REPLY.fullmatch(text_value):
        return ContextResolutionDecision.REJECTED
    if _UNSURE_REPLY.fullmatch(text_value):
        return ContextResolutionDecision.UNSURE
    return None


def _contextual_answer(message_text: str, question) -> tuple[Any, bool] | None:
    text_value = message_text.strip()
    if question.item_type == "choice":
        matching = []
        for option in question.answer_options:
            terms = [option.code, option.display, *option.semantic_aliases]
            if any(term and term in text_value for term in terms):
                matching.append(option.code)
        if len(set(matching)) == 1:
            return matching[0], False
        return None
    if question.item_type == "boolean":
        if _AFFIRMATIVE_REPLY.fullmatch(text_value):
            return True, False
        if _NEGATIVE_REPLY.fullmatch(text_value):
            return False, True
        return None
    if question.item_type == "integer":
        value = count_from_evidence(text_value)
        if value is None:
            token_match = re.search(r"\d+|[零〇一二两三四五六七八九十]+", text_value)
            value = parse_number_token(token_match.group(0)) if token_match else None
        return (value, False) if type(value) is int and value >= 0 else None
    if question.item_type == "quantity":
        if question.link_id == "body-weight":
            token_match = re.search(r"\d+(?:\.\d+)?", text_value)
            value = float(token_match.group(0)) if token_match else None
            unit = "kg"
        else:
            value = millilitres_from_evidence(text_value)
            unit = "mL"
        if value is None:
            token_match = re.search(r"\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+", text_value)
            value = parse_number_token(token_match.group(0)) if token_match else None
        if (
            type(value) not in {int, float}
            or value < 0
            or (question.link_id == "body-weight" and not 20 <= value <= 400)
        ):
            return None
        return {
            "value": value,
            "unit": unit,
            "system": UCUM,
            "code": unit,
        }, False
    return None


def _contextual_answer_display(answer: Any, question) -> str:
    if answer is True:
        return "是"
    if answer is False:
        return "否"
    if isinstance(answer, dict) and "value" in answer:
        return f"{answer['value']} {answer.get('unit', '')}".strip()
    option = next(
        (item for item in question.answer_options if item.code == answer),
        None,
    )
    if option is not None:
        return {
            "Mild": "轻度",
            "Moderate": "中度",
            "Severe": "重度",
        }.get(option.display, option.display or option.code)
    return str(answer)
