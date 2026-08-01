"""Layer-2 orchestration for version-locked, resumable patient follow-up."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from continucare.care_engine.mapping import map_response_to_observations
from continucare.db import utc_now_iso
from continucare.fhir.questionnaires import (
    build_questionnaire_response,
    questionnaire_response_summary,
)
from continucare.models import (
    CareSession,
    CareSessionStatus,
    FollowUpMessage,
    Observation,
)
from continucare.pathways import load_builtin_pathways, load_fhir_artifact
from continucare.pathways.mappings import load_observation_mapping
from continucare.services.audit import record_audit_event


class CareSubmissionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: CareSession
    questionnaire_response: dict[str, Any]
    observations: list[Observation]


class CareEngine:
    """No-LLM execution engine for a governed Questionnaire version."""

    def __init__(self, store):
        self.store = store
        self.registry = load_builtin_pathways()

    def start_or_resume(self, patient_id: str) -> CareSession:
        patient = self._synthetic_patient(patient_id)
        existing = self.store.get_active_care_session(
            patient_id, patient.pathway_code
        )
        if existing:
            return existing

        pathway = self.registry.get(patient.pathway_code)
        questionnaire = self._questionnaire(pathway.code, pathway.version)
        now = utc_now_iso()
        session = CareSession(
            session_id=f"session-{uuid4().hex}",
            patient_id=patient_id,
            pathway_code=pathway.code,
            pathway_version=pathway.version,
            questionnaire_canonical=questionnaire["url"],
            questionnaire_version=questionnaire.get("version", ""),
            answers={},
            created_at=now,
            updated_at=now,
        )
        self.store.save_care_session(session)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="CareSession",
            entity_id=session.session_id,
            event_type="care_session_started",
            actor_type="deterministic_care_engine",
            details={
                "pathway_code": session.pathway_code,
                "pathway_version": session.pathway_version,
                "questionnaire": (
                    f"{session.questionnaire_canonical}|{session.questionnaire_version}"
                ),
            },
        )
        return session

    def questionnaire_for_session(self, session: CareSession) -> dict[str, Any]:
        questionnaire = self._questionnaire(
            session.pathway_code, session.pathway_version
        )
        if (
            questionnaire["url"] != session.questionnaire_canonical
            or questionnaire.get("version", "") != session.questionnaire_version
        ):
            raise ValueError("随访锁定的 Questionnaire 版本不可用")
        return questionnaire

    def save_draft(
        self, session_id: str, answers: dict[str, Any]
    ) -> CareSession:
        session = self._in_progress_session(session_id)
        questionnaire = self.questionnaire_for_session(session)
        # Building an in-progress resource applies the same types, choices and
        # enableWhen checks without requiring completion-only fields.
        build_questionnaire_response(
            questionnaire=questionnaire,
            response_id=f"draft-{session.session_id.removeprefix('session-')}",
            patient_id=session.patient_id,
            authored=utc_now_iso(),
            answers=answers,
            status="in-progress",
        )
        now = utc_now_iso()
        self.store.update_care_session(
            session.session_id,
            answers=answers,
            status=CareSessionStatus.IN_PROGRESS,
            updated_at=now,
        )
        record_audit_event(
            self.store,
            patient_id=session.patient_id,
            entity_type="CareSession",
            entity_id=session.session_id,
            event_type="care_session_draft_saved",
            actor_type="synthetic_patient",
            details={"answered_link_ids": sorted(answers)},
        )
        return self.store.get_care_session(session.session_id)

    def complete(
        self, session_id: str, answers: dict[str, Any]
    ) -> CareSubmissionResult:
        existing = self.store.get_care_session(session_id)
        if existing is None:
            raise ValueError("随访会话不存在")
        if existing.status == CareSessionStatus.COMPLETED:
            if answers != existing.answers:
                raise ValueError("随访已经提交，不能用不同答案重复覆盖")
            return self._completed_result(existing)
        session = self._in_progress_session(session_id)
        if not any(_has_answer(value) for value in answers.values()):
            raise ValueError("请至少回答一个问题后再提交")

        questionnaire = self.questionnaire_for_session(session)
        now = utc_now_iso()
        response_id = f"response-{uuid4().hex}"
        response = build_questionnaire_response(
            questionnaire=questionnaire,
            response_id=response_id,
            patient_id=session.patient_id,
            authored=now,
            answers=answers,
            status="completed",
        )
        pathway = self.registry.get(session.pathway_code, session.pathway_version)
        policy = load_observation_mapping(pathway.observation_mapping_file)
        if (
            policy.pathway_code != session.pathway_code
            or policy.pathway_version != session.pathway_version
        ):
            raise ValueError("Observation mapping policy does not match care session")
        observations = map_response_to_observations(
            response=response,
            questionnaire=questionnaire,
            policy=policy,
        )
        message_text, _ = questionnaire_response_summary(response, questionnaire)
        message = FollowUpMessage(
            message_id=response_id,
            patient_id=session.patient_id,
            message_text=message_text,
            submitted_at=now,
            source="patient_questionnaire_renderer",
            processing_status="structured_complete",
        )
        session.answers = answers
        self.store.complete_care_session_submission(
            session=session,
            message=message,
            questionnaire_response=response,
            questionnaire=questionnaire,
            observations=observations,
            completed_at=now,
        )
        record_audit_event(
            self.store,
            patient_id=session.patient_id,
            entity_type="QuestionnaireResponse",
            entity_id=response_id,
            event_type="questionnaire_response_completed",
            actor_type="synthetic_patient",
            details={
                "session_id": session.session_id,
                "answered_link_ids": sorted(answers),
                "observation_refs": [item.observation_id for item in observations],
                "clinical_assessment": "not_assessed",
            },
        )
        completed = self.store.get_care_session(session.session_id)
        return CareSubmissionResult(
            session=completed,
            questionnaire_response=response,
            observations=observations,
        )

    def stop(self, session_id: str) -> CareSession:
        session = self._in_progress_session(session_id)
        now = utc_now_iso()
        self.store.update_care_session(
            session_id,
            answers=session.answers,
            status=CareSessionStatus.STOPPED,
            updated_at=now,
            completed_at=now,
        )
        record_audit_event(
            self.store,
            patient_id=session.patient_id,
            entity_type="CareSession",
            entity_id=session_id,
            event_type="care_session_stopped",
            actor_type="synthetic_patient",
        )
        return self.store.get_care_session(session_id)

    def _completed_result(self, session: CareSession) -> CareSubmissionResult:
        if not session.questionnaire_response_id:
            raise ValueError("已完成随访缺少 QuestionnaireResponse")
        response = self.store.get_questionnaire_response(
            session.questionnaire_response_id,
            self.questionnaire_for_session(session),
        )
        if response is None:
            raise ValueError("已完成随访的 QuestionnaireResponse 不存在")
        return CareSubmissionResult(
            session=session,
            questionnaire_response=response,
            observations=self.store.list_observations_for_message(
                session.questionnaire_response_id
            ),
        )

    def _in_progress_session(self, session_id: str) -> CareSession:
        session = self.store.get_care_session(session_id)
        if session is None:
            raise ValueError("随访会话不存在")
        if session.status != CareSessionStatus.IN_PROGRESS:
            raise ValueError("只有进行中的随访可以修改或提交")
        self._synthetic_patient(session.patient_id)
        return session

    def _synthetic_patient(self, patient_id: str):
        patient = self.store.get_patient(patient_id)
        if patient is None or not patient.synthetic:
            raise ValueError("Demo 仅接受已登记的合成患者")
        return patient

    def _questionnaire(self, pathway_code: str, pathway_version: str) -> dict[str, Any]:
        pathway = self.registry.get(pathway_code, pathway_version)
        references = [
            item for item in pathway.artifacts if item.resource_type == "Questionnaire"
        ]
        if len(references) != 1:
            raise ValueError(
                f"Pathway {pathway_code} {pathway_version} must publish one Questionnaire"
            )
        reference = references[0]
        questionnaire = load_fhir_artifact(reference.file_name, "Questionnaire")
        if (
            questionnaire.get("url") != reference.canonical
            or questionnaire.get("version") != reference.version
        ):
            raise ValueError(
                "Pathway Questionnaire artifact does not match its manifest reference"
            )
        return questionnaire


def _has_answer(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())
