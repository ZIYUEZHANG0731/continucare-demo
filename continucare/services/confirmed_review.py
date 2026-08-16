"""Application orchestration for patient confirmation to manual nurse review."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from continucare.agents.contracts import (
    CandidateSource,
    SemanticResult,
    TemporalResolutionBasis,
)
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import (
    MODEL_API_MODES,
    MODEL_API_PROVIDERS,
    MODEL_CANDIDATE_SOURCES,
)
from continucare.care_engine import CareEngine
from continucare.db import utc_now_iso
from continucare.layer4.fhir import (
    build_patient_confirmation_provenance,
    build_patient_confirmed_review_task,
)
from continucare.layer4.manual_reviews import admit_final_patient_report
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.models import (
    AuditEvent,
    CareSessionStatus,
    ConfirmedAnswerContext,
    ConfirmedSymptomReport,
    Observation,
)
from continucare.services.patient_checkin import (
    collection_policy_version,
    exact_questionnaire_direct_answer,
    is_single_focused_patient_checkin_task,
    project_patient_checkin,
)
from continucare.services.plan_collection import active_patient_link_ids
from continucare.terminology import terminology_catalog_sha256


class ConfirmedReviewResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    receipt_digest: str
    questionnaire_response: dict[str, Any]
    observations: list[Observation]
    task: dict[str, Any]
    provenance: dict[str, Any]
    idempotent_replay: bool = False


class ConfirmedReviewService:
    """Release one complete, human-confirmed bundle or release nothing."""

    def __init__(self, store, *, care_agent: CareAgentService, care_engine: CareEngine):
        self.store = store
        self.care_agent = care_agent
        self.care_engine = care_engine
        self.layer4_store = Layer4SQLiteStore(store.db_path)

    def accept_all(self, run_id: str, candidate_ids: list[str]) -> ConfirmedReviewResult:
        receipt, record, candidates = self._receipt(run_id, candidate_ids)
        task_id = f"task-manual-review-{receipt[:24]}"
        existing = self.layer4_store.get_fhir_resource("Task", task_id)
        if existing is not None:
            return self._stored_result(receipt, existing)

        confirmed_at = utc_now_iso()
        try:
            candidate_plan = self.care_agent.prepare_confirmed_candidates(
                run_id,
                candidate_ids,
                resolved_at=confirmed_at,
                require_complete_set=True,
            )
            response_id = f"response-confirmed-{receipt[:24]}"
            submission = self.care_engine.prepare_completion(
                record.session_id,
                candidate_plan.answers,
                response_id=response_id,
                completed_at=confirmed_at,
                answer_contexts=candidate_plan.answer_contexts,
                symptom_reports=candidate_plan.symptom_reports,
            )
        except ValueError:
            # A concurrent caller may have committed the exact receipt between
            # the initial replay lookup and the pure preparation reads.
            existing = self.layer4_store.get_fhir_resource("Task", task_id)
            if existing is not None:
                return self._stored_result(receipt, existing)
            raise
        response, observations = admit_final_patient_report(
            patient_id=record.patient_id,
            questionnaire_response=submission.questionnaire_response,
            observations=[item.as_fhir() for item in submission.observations],
        )
        observation_refs = [f"Observation/{item['id']}" for item in observations]
        pathway_reference = (
            f"urn:continucare:pathway:{submission.session.pathway_code}"
            f"|{submission.session.pathway_version}"
        )
        task = build_patient_confirmed_review_task(
            patient_id=record.patient_id,
            receipt_digest=receipt,
            questionnaire_response_reference=f"QuestionnaireResponse/{response['id']}",
            observation_references=observation_refs,
            pathway_reference=pathway_reference,
            authored_on=confirmed_at,
            task_id=task_id,
        )
        confirmation_audit = self._audit(
            receipt,
            "confirmation",
            patient_id=record.patient_id,
            entity_type="AgentRun",
            entity_id=record.run_id,
            event_type="semantic_candidate_patient_decision",
            actor_type="synthetic_patient",
            created_at=confirmed_at,
            details={
                "session_id": record.session_id,
                "decision": "accepted_for_manual_review",
                "candidate_ids": [item.candidate_id for item in candidates],
                "confirmed_link_ids": [item.link_id for item in candidates],
                "clinical_assessment": "not_assessed",
            },
        )
        response_audit = self._audit(
            receipt,
            "response",
            patient_id=record.patient_id,
            entity_type="QuestionnaireResponse",
            entity_id=response["id"],
            event_type="questionnaire_response_completed",
            actor_type="synthetic_patient",
            created_at=confirmed_at,
            details={
                "session_id": record.session_id,
                "answered_link_ids": sorted(candidate_plan.answers),
                "observation_refs": [item["id"] for item in observations],
                "clinical_assessment": "not_assessed",
            },
        )
        task_audit = self._audit(
            receipt,
            "task",
            patient_id=record.patient_id,
            entity_type="Task",
            entity_id=task["id"],
            event_type="manual_review_task_created",
            actor_type="deterministic_workflow",
            created_at=confirmed_at,
            details={
                "questionnaire_response_ref": response["id"],
                "observation_refs": [item["id"] for item in observations],
                "priority": "routine",
                "clinical_assessment": "not_assessed",
                "authorization_basis": "explicit_patient_confirmation",
            },
        )
        provenance = build_patient_confirmation_provenance(
            target_references=[
                f"QuestionnaireResponse/{response['id']}",
                *observation_refs,
                f"Task/{task['id']}",
            ],
            entity_source_references=[
                f"QuestionnaireResponse/{response['id']}",
                *observation_refs,
                f"urn:continucare:audit-event:{confirmation_audit.event_id}",
            ],
            confirmed_at=confirmed_at,
            patient_id=record.patient_id,
            provenance_id=f"provenance-confirmed-{receipt[:24]}",
        )
        created = self.store.persist_confirmed_review_bundle(
            session=submission.session,
            message=submission.message,
            questionnaire_response=response,
            questionnaire=submission.questionnaire,
            observations=submission.observations,
            answer_contexts=candidate_plan.answer_contexts,
            symptom_reports=candidate_plan.symptom_reports,
            action_ids=[item.candidate_id for item in candidates],
            source_run_id=record.run_id,
            resolved_at=confirmed_at,
            audit_events=[confirmation_audit, response_audit, task_audit],
            layer4_resources=[task, provenance],
            expected_session=candidate_plan.session,
        )
        if not created:
            stored = self.layer4_store.get_fhir_resource("Task", task_id)
            if stored is None:
                raise RuntimeError("幂等提交未找到已存在的护士复核任务")
            return self._stored_result(receipt, stored)
        return ConfirmedReviewResult(
            receipt_digest=receipt,
            questionnaire_response=response,
            observations=submission.observations,
            task=task,
            provenance=provenance,
        )

    def submit_confirmed_draft(
        self,
        session_id: str,
        *,
        require_single_final_confirmation: bool = False,
    ) -> ConfirmedReviewResult:
        """Atomically release an explicitly confirmed multi-turn draft."""

        session = self.store.get_care_session(session_id)
        if session is None or session.status != CareSessionStatus.IN_PROGRESS:
            raise ValueError("今天的随访已变化，请刷新后重试")
        questionnaire = self.care_engine.questionnaire_for_session(session)
        policy_version = collection_policy_version(session)
        collection_resolutions = self.store.current_collection_resolutions(session_id)
        collection_link_ids = active_patient_link_ids(
            self.store.db_path,
            patient_id=session.patient_id,
            pathway_code=session.pathway_code,
            questionnaire=questionnaire,
        )
        allowed_links = set(collection_link_ids)
        unexpected_answers = set(session.answers) - allowed_links - {"free-text-report"}
        unexpected_resolutions = set(collection_resolutions) - allowed_links
        if unexpected_answers or unexpected_resolutions:
            raise ValueError("草稿包含不在当前医生方案内的结构化指标，请重新开始本轮随访")
        checkin = project_patient_checkin(
            session,
            questionnaire,
            explicit_unknown_link_ids={
                link_id
                for link_id, resolution in collection_resolutions.items()
                if resolution == "explicit_unknown"
            },
            collection_link_ids=collection_link_ids,
        )
        if not checkin.ready_to_submit:
            raise ValueError("今天需要采集的指标尚未全部确认")
        if require_single_final_confirmation:
            if self.store.list_active_answer_contexts(
                session_id
            ) or self.store.list_active_symptom_reports(session_id):
                raise ValueError(
                    "旧版逐轮确认记录不能静默解释为统一最终确认，请明确重新开始"
                )
            provisional_contexts = (
                self.store.list_active_provisional_answer_contexts(session_id)
            )
            provisional_reports = (
                self.store.list_active_provisional_symptom_reports(session_id)
            )
            contexts = [
                ConfirmedAnswerContext(
                    answer_context_id=(
                        "answer-context-final-"
                        + hashlib.sha256(
                            item.draft_context_id.encode("utf-8")
                        ).hexdigest()[:24]
                    ),
                    **item.model_dump(exclude={"draft_context_id", "status"}),
                    status="active",
                )
                for item in provisional_contexts
            ]
            reports = [
                ConfirmedSymptomReport(
                    report_id=(
                        "symptom-report-final-"
                        + hashlib.sha256(
                            item.draft_report_id.encode("utf-8")
                        ).hexdigest()[:24]
                    ),
                    **item.model_dump(exclude={"draft_report_id", "status"}),
                    status="active",
                )
                for item in provisional_reports
            ]
            revision_contexts = self.store.list_provisional_answer_context_history(
                session_id
            )
            (
                draft_event_ids,
                draft_action_ids,
                accepted_draft_action_ids,
                draft_run_ids,
                provisional_boundary,
            ) = (
                self._validated_provisional_sources(
                    session_id=session_id,
                    contexts=provisional_contexts,
                    reports=provisional_reports,
                )
            )
        else:
            contexts = self.store.list_active_answer_contexts(session_id)
            reports = self.store.list_active_symptom_reports(session_id)
            revision_contexts = self.store.list_answer_context_history(session_id)
            draft_event_ids, draft_action_ids, accepted_draft_action_ids, draft_run_ids = (
                [],
                [],
                [],
                set(),
            )
            provisional_boundary = None
        unexpected_contexts = {
            item.link_id
            for item in contexts
            if item.link_id not in allowed_links and item.link_id != "free-text-report"
        }
        if unexpected_contexts:
            raise ValueError("草稿包含不在当前医生方案内的结构化指标，请重新开始本轮随访")
        revision_event_ids, lineage_run_ids = _validated_revision_lineage(
            session_id=session_id,
            contexts=revision_contexts,
            audit_events=self.store.list_audit_events(session.patient_id),
        )
        lineage_event_ids = sorted(set(revision_event_ids) | set(draft_event_ids))
        source_run_ids = sorted(
            {item.source_run_id for item in [*contexts, *reports]}
            | lineage_run_ids
            | draft_run_ids
        )
        runs = self.store.list_agent_runs(session_id)
        if not source_run_ids or not runs:
            raise ValueError("已确认草稿缺少模型来源记录")
        record_by_id = {item.run_id: item for item in runs}
        if any(run_id not in record_by_id for run_id in source_run_ids):
            raise ValueError("已确认草稿的来源记录不完整")
        record = runs[0]
        identity = {
            "patient_id": session.patient_id,
            "session_id": session.session_id,
            "pathway": f"{session.pathway_code}|{session.pathway_version}",
            "policy_version": policy_version,
            "answers": session.answers,
            "answer_contexts": sorted(item.answer_context_id for item in contexts),
            "symptom_reports": sorted(item.report_id for item in reports),
            "source_run_ids": source_run_ids,
            "revision_lineage_event_ids": lineage_event_ids,
            "collection_resolutions": collection_resolutions,
        }
        if require_single_final_confirmation:
            identity["provisional_action_ids"] = draft_action_ids
            identity["accepted_provisional_action_ids"] = accepted_draft_action_ids
        receipt = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        task_id = f"task-manual-review-{receipt[:24]}"
        existing = self.layer4_store.get_fhir_resource("Task", task_id)
        if existing is not None:
            return self._stored_result(receipt, existing)

        confirmed_at = utc_now_iso()
        response_id = f"response-confirmed-{receipt[:24]}"
        submission = self.care_engine.prepare_completion(
            session_id,
            session.answers,
            response_id=response_id,
            completed_at=confirmed_at,
            answer_contexts=contexts,
            symptom_reports=reports,
        )
        response, observations = admit_final_patient_report(
            patient_id=session.patient_id,
            questionnaire_response=submission.questionnaire_response,
            observations=[item.as_fhir() for item in submission.observations],
        )
        observation_refs = [f"Observation/{item['id']}" for item in observations]
        pathway_reference = (
            f"urn:continucare:pathway:{session.pathway_code}|{session.pathway_version}"
        )
        task = build_patient_confirmed_review_task(
            patient_id=session.patient_id,
            receipt_digest=receipt,
            questionnaire_response_reference=f"QuestionnaireResponse/{response['id']}",
            observation_references=observation_refs,
            pathway_reference=pathway_reference,
            authored_on=confirmed_at,
            task_id=task_id,
        )
        confirmation_audit = self._audit(
            receipt,
            "confirmation",
            patient_id=session.patient_id,
            entity_type="CareSession",
            entity_id=session.session_id,
            event_type="patient_checkin_submitted",
            actor_type="synthetic_patient",
            created_at=confirmed_at,
            details={
                "session_id": session.session_id,
                "decision": "submitted_for_manual_review",
                "collection_policy_version": policy_version,
                "source_run_ids": source_run_ids,
                "revision_lineage_event_ids": lineage_event_ids,
                "answered_link_ids": sorted(session.answers),
                "collection_resolutions": collection_resolutions,
                "clinical_assessment": "not_assessed",
                **(
                    {"provisional_action_ids": draft_action_ids}
                    if require_single_final_confirmation
                    else {}
                ),
                **(
                    {"accepted_provisional_action_ids": accepted_draft_action_ids}
                    if require_single_final_confirmation
                    else {}
                ),
            },
        )
        response_audit = self._audit(
            receipt,
            "response",
            patient_id=session.patient_id,
            entity_type="QuestionnaireResponse",
            entity_id=response["id"],
            event_type="questionnaire_response_completed",
            actor_type="synthetic_patient",
            created_at=confirmed_at,
            details={
                "session_id": session.session_id,
                "answered_link_ids": sorted(session.answers),
                "observation_refs": [item["id"] for item in observations],
                "clinical_assessment": "not_assessed",
            },
        )
        task_audit = self._audit(
            receipt,
            "task",
            patient_id=session.patient_id,
            entity_type="Task",
            entity_id=task["id"],
            event_type="manual_review_task_created",
            actor_type="deterministic_workflow",
            created_at=confirmed_at,
            details={
                "questionnaire_response_ref": response["id"],
                "observation_refs": [item["id"] for item in observations],
                "priority": "routine",
                "clinical_assessment": "not_assessed",
                "authorization_basis": "explicit_patient_confirmation",
            },
        )
        provenance = build_patient_confirmation_provenance(
            target_references=[
                f"QuestionnaireResponse/{response['id']}",
                *observation_refs,
                f"Task/{task['id']}",
            ],
            entity_source_references=[
                f"QuestionnaireResponse/{response['id']}",
                *observation_refs,
                *(f"urn:continucare:agent-run:{run_id}" for run_id in source_run_ids),
                *(
                    f"urn:continucare:audit-event:{event_id}"
                    for event_id in lineage_event_ids
                ),
                f"urn:continucare:audit-event:{confirmation_audit.event_id}",
            ],
            confirmed_at=confirmed_at,
            patient_id=session.patient_id,
            provenance_id=f"provenance-confirmed-{receipt[:24]}",
        )
        created = self.store.persist_confirmed_review_bundle(
            session=submission.session,
            message=submission.message,
            questionnaire_response=response,
            questionnaire=submission.questionnaire,
            observations=submission.observations,
            answer_contexts=(contexts if require_single_final_confirmation else []),
            symptom_reports=(reports if require_single_final_confirmation else []),
            action_ids=[],
            source_run_id=record.run_id,
            resolved_at=confirmed_at,
            audit_events=[confirmation_audit, response_audit, task_audit],
            layer4_resources=[task, provenance],
            expected_session=session,
            finalize_existing_draft=True,
            collection_resolutions=collection_resolutions,
            collection_policy_version=policy_version,
            revision_lineage_event_ids=revision_event_ids,
            promote_provisional_draft=require_single_final_confirmation,
            provisional_action_ids=draft_action_ids,
            accepted_provisional_action_ids=accepted_draft_action_ids,
            provisional_draft_event_ids=draft_event_ids,
            provisional_boundary=provisional_boundary,
        )
        if not created:
            stored = self.layer4_store.get_fhir_resource("Task", task_id)
            if stored is None:
                raise RuntimeError("幂等提交未找到已存在的护士复核任务")
            return self._stored_result(receipt, stored)
        return ConfirmedReviewResult(
            receipt_digest=receipt,
            questionnaire_response=response,
            observations=submission.observations,
            task=task,
            provenance=provenance,
        )

    def _validated_provisional_sources(
        self,
        *,
        session_id: str,
        contexts,
        reports,
    ) -> tuple[list[str], list[str], list[str], set[str], dict[str, Any]]:
        """Prove every final value came from a governed, unconfirmed model draft."""

        actions = self.store.list_provisional_actions(session_id)
        if not actions:
            raise ValueError("统一确认草稿缺少待确认动作来源")
        session = self.store.get_care_session(session_id)
        if session is None:
            raise ValueError("统一确认草稿缺少随访会话")
        catalog = self.care_agent.terminology.catalog
        fixed_catalog = getattr(catalog, "fixed_catalog", None)
        dynamic_catalog = getattr(catalog, "dynamic_catalog", None)
        if (
            fixed_catalog is None
            or dynamic_catalog is None
        ):
            raise ValueError("统一确认必须使用锁定的模型与组合术语边界")
        runs = {
            item.run_id: item for item in self.store.list_agent_runs(session_id)
        }
        source_runs = [runs.get(action["source_run_id"]) for action in actions]
        model_boundaries = {
            (run.model_provider, run.model_name, run.prompt_version)
            for run in source_runs
            if run is not None
        }
        if any(run is None for run in source_runs) or len(model_boundaries) != 1:
            raise ValueError("统一确认草稿缺少唯一模型运行边界")
        model_provider, model_name, prompt_version = next(iter(model_boundaries))
        if (
            model_provider not in MODEL_API_PROVIDERS
            or not model_name
            or not prompt_version
        ):
            raise ValueError("统一确认必须使用受支持的模型运行边界")
        provisional_boundary = {
            "model_provider": model_provider,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "knowledge_release_id": session.knowledge_release_id,
            "terminology_catalog_id": catalog.catalog_id,
            "terminology_catalog_version": catalog.version,
            "terminology_catalog_sha256": terminology_catalog_sha256(catalog),
            "fixed_source": {
                "catalog_id": fixed_catalog.catalog_id,
                "version": fixed_catalog.version,
                "sha256": terminology_catalog_sha256(fixed_catalog),
                "status": fixed_catalog.status,
            },
            "dynamic_source": {
                "catalog_id": dynamic_catalog.catalog_id,
                "version": dynamic_catalog.version,
                "sha256": terminology_catalog_sha256(dynamic_catalog),
                "status": dynamic_catalog.status,
            },
        }
        events = self.store.list_audit_events(
            next(iter(runs.values())).patient_id if runs else None
        )
        stage_events = [
            event
            for event in events
            if event.event_type == "semantic_candidate_staged_to_draft"
            and event.entity_type == "AgentRun"
            and event.entity_id in runs
        ]
        material_events = [
            event
            for event in events
            if event.event_type == "care_session_provisional_draft_saved"
            and event.entity_type == "CareSession"
            and event.entity_id == session_id
        ]
        context_digests: dict[str, list[tuple[str, AuditEvent]]] = {}
        report_digests: dict[str, list[tuple[str, AuditEvent]]] = {}
        for event in material_events:
            details = event.details_json
            context_map = details.get("draft_context_sha256")
            report_map = details.get("draft_report_sha256")
            if (
                event.actor_type != "deterministic_workflow"
                or details.get("session_id") != session_id
                or details.get("confirmation_status") != "pending_final_review"
                or not isinstance(context_map, dict)
                or not isinstance(report_map, dict)
                or sorted(context_map) != sorted(details.get("draft_context_ids", []))
                or sorted(report_map) != sorted(details.get("draft_report_ids", []))
            ):
                raise ValueError("待确认草稿内容摘要审计不完整")
            for material_id, digest in context_map.items():
                context_digests.setdefault(material_id, []).append((digest, event))
            for material_id, digest in report_map.items():
                report_digests.setdefault(material_id, []).append((digest, event))
        for context in contexts:
            matches = context_digests.get(context.draft_context_id, [])
            if (
                len(matches) != 1
                or matches[0][0] != _provisional_material_sha256(context)
                or matches[0][1].details_json.get("source_run_id")
                != context.source_run_id
            ):
                raise ValueError("待确认问卷草稿内容在生成后发生变化")
        for report in reports:
            matches = report_digests.get(report.draft_report_id, [])
            if (
                len(matches) != 1
                or matches[0][0] != _provisional_material_sha256(report)
                or matches[0][1].details_json.get("source_run_id")
                != report.source_run_id
            ):
                raise ValueError("待确认症状草稿内容在生成后发生变化")
        events_by_action: dict[str, list[AuditEvent]] = {}
        for event in stage_events:
            details = event.details_json
            if (
                event.actor_type != "deterministic_workflow"
                or details.get("session_id") != session_id
                or details.get("confirmation_status") != "pending_final_review"
            ):
                raise ValueError("待确认草稿审计边界不完整")
            for action_id in details.get("action_ids", []):
                events_by_action.setdefault(action_id, []).append(event)

        expected_by_run: dict[str, list[tuple[Any, str]]] = {}
        for action in actions:
            run = runs.get(action["source_run_id"])
            if run is None or len(events_by_action.get(action["action_id"], [])) != 1:
                raise ValueError("待确认草稿动作缺少唯一模型来源审计")
            stage_event = events_by_action[action["action_id"]][0]
            stage_details = stage_event.details_json
            if (
                stage_event.entity_id != run.run_id
                or stage_details.get("action_type") != action["action_type"]
                or stage_details.get("decision") != action["decision"]
                or stage_details.get("option_id") != action["option_id"]
            ):
                raise ValueError("待确认草稿动作与审计载荷不一致")
            result = SemanticResult.model_validate(run.output_json)
            extraction = [
                item for item in result.stage_traces if item.stage == "care_extraction"
            ]
            if (
                run.model_provider != provisional_boundary["model_provider"]
                or run.model_name != provisional_boundary["model_name"]
                or run.prompt_version != provisional_boundary["prompt_version"]
                or run.knowledge_release_id
                != provisional_boundary["knowledge_release_id"]
                or run.terminology_catalog_id
                != provisional_boundary["terminology_catalog_id"]
                or run.terminology_catalog_version
                != provisional_boundary["terminology_catalog_version"]
                or run.terminology_catalog_sha256
                != provisional_boundary["terminology_catalog_sha256"]
                or result.mode not in MODEL_API_MODES
                or len(extraction) != 1
                or extraction[0].mode not in MODEL_API_MODES
                or extraction[0].model_provider != run.model_provider
                or extraction[0].model_name != run.model_name
                or extraction[0].prompt_version != run.prompt_version
            ):
                raise ValueError("待确认草稿的模型来源不可信")
            candidate = None
            if action["action_type"] == "candidate":
                candidate = next(
                    (
                        item
                        for item in result.candidates
                        if item.candidate_id == action["action_id"]
                    ),
                    None,
                )
            elif action["decision"] == "drafted":
                candidate = self.care_agent.prepare_clarification_candidate(
                    run.run_id,
                    action["action_id"],
                    action["option_id"],
                    temporal_basis=TemporalResolutionBasis.PATIENT_SELECTION,
                )
            if action["decision"] == "drafted" and candidate is None:
                raise ValueError("待确认草稿动作缺少可重放候选")
            if candidate is not None:
                match = candidate.terminology_match
                source_boundary = provisional_boundary[
                    "dynamic_source"
                    if candidate.link_id.startswith("patient-reported-symptom::")
                    else "fixed_source"
                ]
                if (
                    match is None
                    or match.catalog_id
                    != provisional_boundary["terminology_catalog_id"]
                    or match.catalog_version
                    != provisional_boundary["terminology_catalog_version"]
                    or match.source_catalog_id != source_boundary["catalog_id"]
                    or match.source_catalog_version != source_boundary["version"]
                    or match.source_catalog_sha256 != source_boundary["sha256"]
                    or match.source_catalog_status != source_boundary["status"]
                ):
                    raise ValueError("待确认草稿候选的术语版本边界不可信")
                dynamic_catalog_candidate = (
                    candidate.link_id.startswith("patient-reported-symptom::")
                    and candidate.source_mode == CandidateSource.DETERMINISTIC_CATALOG
                    and candidate.terminology_match is not None
                    and bool(candidate.terminology_match.source_catalog_id)
                    and bool(candidate.terminology_match.source_catalog_version)
                    and bool(candidate.terminology_match.source_catalog_sha256)
                )
                patient_selection_candidate = (
                    candidate.source_mode == CandidateSource.PATIENT_SELECTION
                    and not candidate.link_id.startswith("patient-reported-symptom::")
                    and candidate.evidence_text == run.input_text
                    and candidate.evidence_start == 0
                    and candidate.evidence_end == len(run.input_text)
                    and result.task_id == run.task_id
                    and is_single_focused_patient_checkin_task(
                        run.task_id, candidate.link_id
                    )
                    and candidate.answer
                    == exact_questionnaire_direct_answer(
                        self.care_engine.questionnaire_for_session(session),
                        candidate.link_id,
                        run.input_text,
                    )
                    and result.ignored_reasons.count(
                        "focused_governed_patient_selection_used"
                    )
                    == 1
                )
                if (
                    candidate.source_mode not in MODEL_CANDIDATE_SOURCES
                    and not dynamic_catalog_candidate
                    and not patient_selection_candidate
                ):
                    raise ValueError("待确认草稿候选缺少可验证来源")
                expected_by_run.setdefault(run.run_id, []).append(
                    (candidate, action["action_id"])
                )

        accepted_action_ids: set[str] = set()
        for context in contexts:
            if context.link_id == "free-text-report":
                run = runs.get(context.source_run_id)
                if run is None or context.raw_text != run.input_text:
                    raise ValueError("患者原话草稿缺少不可变来源")
                continue
            matches = [
                (item, action_id)
                for item, action_id in expected_by_run.get(context.source_run_id, [])
                if item.link_id == context.link_id
                and item.answer == context.answer
                and item.evidence_text in context.raw_text
            ]
            if len(matches) != 1:
                raise ValueError("待确认问卷草稿与模型候选不一致")
            accepted_action_ids.add(matches[0][1])
        for report in reports:
            matches = [
                (item, action_id)
                for item, action_id in expected_by_run.get(report.source_run_id, [])
                if item.terminology_match is not None
                and item.terminology_match.concept_id == report.concept_id
                and item.evidence_text == report.evidence_text
            ]
            if len(matches) != 1:
                raise ValueError("待确认症状草稿与模型术语候选不一致")
            accepted_action_ids.add(matches[0][1])
        event_ids = sorted(
            {event.event_id for event in [*stage_events, *material_events]}
        )
        action_ids = sorted(action["action_id"] for action in actions)
        if not accepted_action_ids or accepted_action_ids - set(action_ids):
            raise ValueError("最终资料卡缺少待确认动作来源")
        return (
            event_ids,
            action_ids,
            sorted(accepted_action_ids),
            {action["source_run_id"] for action in actions},
            provisional_boundary,
        )

    def _receipt(self, run_id: str, candidate_ids: list[str]):
        record = self.store.get_agent_run(run_id)
        if record is None:
            raise ValueError("Agent 运行记录不存在")
        result = SemanticResult.model_validate(record.output_json)
        available = {item.candidate_id: item for item in result.candidates}
        if not candidate_ids or set(candidate_ids) != set(available):
            raise ValueError("必须一次处理本轮全部候选")
        candidates = [available[item_id] for item_id in sorted(candidate_ids)]
        session = self.store.get_care_session(record.session_id)
        if session is None or session.patient_id != record.patient_id:
            raise ValueError("Agent 运行记录与随访会话不一致")
        identity = {
            "patient_id": record.patient_id,
            "session_id": record.session_id,
            "pathway": f"{session.pathway_code}|{session.pathway_version}",
            "run_id": record.run_id,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "link_id": item.link_id,
                    "answer": item.answer,
                    "evidence_text": item.evidence_text,
                }
                for item in candidates
            ],
        }
        encoded = json.dumps(
            identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), record, candidates

    def _stored_result(self, receipt: str, task: dict[str, Any]) -> ConfirmedReviewResult:
        response_id = f"response-confirmed-{receipt[:24]}"
        session = next(
            (
                item
                for item in self.store.list_care_sessions(task["for"]["reference"].split("/", 1)[1])
                if item.questionnaire_response_id == response_id
            ),
            None,
        )
        if session is None or session.status != CareSessionStatus.COMPLETED:
            raise RuntimeError("已存在任务缺少完成的随访证据")
        response = self.store.get_questionnaire_response(response_id)
        provenance = self.layer4_store.get_fhir_resource(
            "Provenance", f"provenance-confirmed-{receipt[:24]}"
        )
        if response is None or provenance is None:
            raise RuntimeError("已存在任务的证据链不完整")
        return ConfirmedReviewResult(
            receipt_digest=receipt,
            questionnaire_response=response,
            observations=self.store.list_observations_for_message(response_id),
            task=task,
            provenance=provenance,
            idempotent_replay=True,
        )

    @staticmethod
    def _audit(
        receipt: str,
        suffix: str,
        *,
        patient_id: str,
        entity_type: str,
        entity_id: str,
        event_type: str,
        actor_type: str,
        created_at: str,
        details: dict[str, Any],
    ) -> AuditEvent:
        event_hash = hashlib.sha256(f"{receipt}|{suffix}".encode("utf-8")).hexdigest()
        return AuditEvent(
            event_id=f"audit_{event_hash[:32]}",
            patient_id=patient_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor_type=actor_type,
            details_json=details,
            created_at=created_at,
        )


def _provisional_material_sha256(item: Any) -> str:
    payload = json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_revision_lineage(
    *,
    session_id: str,
    contexts,
    audit_events: list[AuditEvent],
) -> tuple[list[str], set[str]]:
    """Validate every correction edge and dependency-invalidation chain."""

    def node(source_run_id: str, answer: Any) -> tuple[str, str]:
        return (
            source_run_id,
            json.dumps(
                answer,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    context_by_node: dict[tuple[str, tuple[str, str]], list[Any]] = {}
    for context in contexts:
        key = (context.link_id, node(context.source_run_id, context.answer))
        context_by_node.setdefault(key, []).append(context)

    correction_details: list[dict[str, Any]] = []
    invalidation_details: list[dict[str, Any]] = []
    selection_details: list[dict[str, Any]] = []
    lineage_event_ids: set[str] = set()
    lineage_run_ids: set[str] = set()
    for event in audit_events:
        if event.entity_type != "CareSession" or event.entity_id != session_id:
            continue
        details = event.details_json
        if event.event_type in {
            "patient_answer_corrected",
            "patient_draft_answer_revised",
        }:
            if details.get("session_id") != session_id:
                raise ValueError("患者修订审计与随访会话不一致")
            correction_details.append(details)
            lineage_event_ids.add(event.event_id)
        elif event.event_type in {
            "patient_answers_dependency_invalidated",
            "patient_draft_dependencies_invalidated",
        }:
            if details.get("session_id") != session_id:
                raise ValueError("条件失效审计与随访会话不一致")
            invalidations = details.get("invalidations")
            if not isinstance(invalidations, list) or not invalidations:
                raise ValueError("条件失效审计缺少字段明细")
            for item in invalidations:
                invalidation_details.append(
                    {
                        **item,
                        "correction_source_run_id": details.get(
                            "correction_source_run_id"
                        ),
                    }
                )
            lineage_event_ids.add(event.event_id)
        elif event.event_type == "patient_answer_selected":
            if details.get("session_id") != session_id:
                raise ValueError("患者选项审计与随访会话不一致")
            selections = details.get("selections")
            if not isinstance(selections, list) or not selections:
                raise ValueError("患者选项审计缺少字段明细")
            selection_details.append(details)
            lineage_event_ids.add(event.event_id)
        elif event.event_type == "patient_draft_symptom_removed":
            if (
                event.actor_type != "synthetic_patient"
                or details.get("session_id") != session_id
                or not details.get("draft_report_id")
                or not details.get("concept_id")
                or not details.get("source_run_id")
                or details.get("confirmation_status")
                != "removed_before_final_confirmation"
            ):
                raise ValueError("症状草稿移除审计不完整")
            lineage_event_ids.add(event.event_id)
            lineage_run_ids.add(details["source_run_id"])

    for details in selection_details:
        source_run_id = details.get("source_run_id")
        if not source_run_id:
            raise ValueError("患者选项审计缺少来源 AgentRun")
        for selection in details["selections"]:
            link_id = selection.get("link_id", "")
            candidate_id = selection.get("candidate_id", "")
            if not link_id or not candidate_id:
                raise ValueError("患者选项审计缺少候选或问卷字段")
            selected_node = node(
                source_run_id, selection.get("patient_selected_answer")
            )
            selected_contexts = context_by_node.get((link_id, selected_node), [])
            if len(selected_contexts) != 1:
                raise ValueError("患者选择与确认答案来源不一致")
        lineage_run_ids.add(source_run_id)

    outgoing: dict[str, dict[tuple[str, str], tuple[str, str]]] = {}
    incoming: dict[str, dict[tuple[str, str], tuple[str, str]]] = {}
    invalidated: dict[str, set[tuple[str, str]]] = {}
    for details in correction_details:
        link_id = details.get("link_id", "")
        if not link_id:
            raise ValueError("患者修订来源链缺少问卷字段")
        previous_run = details.get("previous_source_run_id")
        correction_run = details.get("correction_source_run_id")
        if (
            not previous_run
            or not correction_run
            or previous_run == correction_run
            or not str(details.get("raw_text", "")).strip()
        ):
            raise ValueError("患者修订来源链缺少前后 AgentRun")
        prior = node(previous_run, details.get("previous_answer"))
        replacement = node(correction_run, details.get("replacement_answer"))
        prior_contexts = context_by_node.get((link_id, prior), [])
        replacement_contexts = context_by_node.get((link_id, replacement), [])
        if (
            len(prior_contexts) != 1
            or prior_contexts[0].status != "superseded"
            or len(replacement_contexts) != 1
        ):
            raise ValueError("患者修订来源链与历史答案版本不一致")
        link_outgoing = outgoing.setdefault(link_id, {})
        link_incoming = incoming.setdefault(link_id, {})
        if prior in link_outgoing or replacement in link_incoming:
            raise ValueError("患者修订来源链存在分叉")
        link_outgoing[prior] = replacement
        link_incoming[replacement] = prior
        lineage_run_ids.update({previous_run, correction_run})

    for details in invalidation_details:
        link_id = details.get("link_id", "")
        previous_run = details.get("previous_source_run_id")
        correction_run = details.get("correction_source_run_id")
        if not link_id or not previous_run or not correction_run:
            raise ValueError("条件失效来源链缺少前后 AgentRun")
        prior = node(previous_run, details.get("previous_answer"))
        prior_contexts = context_by_node.get((link_id, prior), [])
        if (
            len(prior_contexts) != 1
            or prior_contexts[0].status != "superseded"
        ):
            raise ValueError("条件失效来源链与历史答案版本不一致")
        invalidated.setdefault(link_id, set()).add(prior)
        lineage_run_ids.update({previous_run, correction_run})

    for link_id, link_outgoing in outgoing.items():
        link_incoming = incoming.get(link_id, {})
        starts = set(link_outgoing) - set(link_incoming)
        if not starts:
            raise ValueError("患者修订来源链形成循环")
        visited: set[tuple[str, str]] = set()
        for start in starts:
            current = start
            while current in link_outgoing:
                if current in visited:
                    raise ValueError("患者修订来源链形成循环")
                visited.add(current)
                current = link_outgoing[current]
            if current in invalidated.get(link_id, set()):
                continue
            tail_contexts = context_by_node.get((link_id, current), [])
            if len(tail_contexts) != 1 or tail_contexts[0].status != "active":
                raise ValueError("患者修订链尾不是唯一当前答案或已审计失效")
        if visited != set(link_outgoing):
            raise ValueError("患者修订来源链存在缺口或循环")
        if set(link_outgoing) & invalidated.get(link_id, set()):
            raise ValueError("已失效的答案版本仍有后续修订分支")

    for link_id, nodes in invalidated.items():
        for invalidated_node in nodes:
            contexts_for_node = context_by_node.get((link_id, invalidated_node), [])
            if any(item.status == "active" for item in contexts_for_node):
                raise ValueError("已审计失效的答案仍被标记为当前版本")
    return sorted(lineage_event_ids), lineage_run_ids
