"""Patient-confirmed, post-check-in supplemental reporting for the synthetic MVP."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import (
    CandidateSource,
    ClarificationKind,
    ClarificationRequest,
    SemanticCandidate,
    SemanticResult,
    SemanticStatus,
)
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import (
    MODEL_API_MODES,
    MODEL_API_PROVIDERS,
    MODEL_CANDIDATE_SOURCES,
    SemanticModelConfig,
    UnconfiguredModelAdapter,
)
from continucare.care_engine import CareEngine
from continucare.care_engine.mapping import map_response_to_observations
from continucare.db import connect, utc_now_iso
from continucare.fhir.observations import build_patient_reported_observation
from continucare.fhir.questionnaires import (
    build_questionnaire_response,
    questionnaire_response_summary,
)
from continucare.fhir.references import validate_questionnaire_response_against_questionnaire
from continucare.fhir.r4 import validate_r4_resource
from continucare.fhir.terminology import CodingDefinition
from continucare.layer4.fhir import (
    build_patient_confirmation_provenance,
    validate_layer4_fhir_resource,
)
from continucare.layer4.manual_reviews import admit_final_patient_report
from continucare.models import (
    AuditEvent,
    CareSession,
    CareSessionStatus,
    ConfidenceTier,
    ConfirmedAnswerContext,
    ConfirmedSymptomReport,
    FollowUpMessage,
    Observation,
)
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoStartError,
    _atomic_stage_replace,
    _competition_mimo_adapter,
    demo_write_guard,
    read_competition_demo,
)
from continucare.services.patient_checkin import (
    CORE_LINK_IDS,
    validate_synthetic_chat_message,
)
from continucare.terminology import (
    load_glp1_symptom_catalog,
    load_supplemental_terminology_backend,
    terminology_catalog_sha256,
)
from continucare.terminology.catalog import DYNAMIC_LINK_PREFIX


SUPPLEMENTAL_TASK_PREFIX = "supplemental:"


class SupplementalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    patient_id: str
    session_id: str
    anchor_session_id: str
    source_run_id: str
    original_text: str
    structured_items: tuple[dict[str, Any], ...] = ()
    status: str
    questionnaire_response_id: str | None = None
    observation_ids: tuple[str, ...] = ()
    provenance_id: str | None = None
    report_kind: str = "patient_supplemental"
    handoff_reason_code: str | None = None
    handoff_policy_version: str | None = None
    created_at: str
    reviewed_at: str | None = None
    review_note: str | None = None


class SupplementalProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    generation: str
    pending_run_id: str | None = None
    pending_text: str | None = None
    pending_items: tuple[dict[str, Any], ...] = ()
    pending_clarifications: tuple[dict[str, Any], ...] = ()
    reports: tuple[SupplementalReport, ...] = ()
    integrity_issue: str | None = None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _projection_generation(payload: Any) -> str:
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:32]
    return f"supplemental:{digest}"


def _trusted_supplemental_match(match: Any, *, dynamic: bool) -> bool:
    """Prove both the composite boundary and the exact delegated source."""

    backend = load_supplemental_terminology_backend()
    source = (
        backend.catalog.dynamic_catalog
        if dynamic
        else backend.catalog.fixed_catalog
    )
    return bool(
        match is not None
        and match.catalog_id == backend.catalog.catalog_id
        and match.catalog_version == backend.catalog.version
        and match.source_catalog_id == source.catalog_id
        and match.source_catalog_version == source.version
        and match.source_catalog_sha256 == terminology_catalog_sha256(source)
        and match.source_catalog_status == source.status
        and (
            not dynamic
            or (
                match.approval_status == "prototype-verified"
                and match.target_hospital_validation_required is True
            )
        )
    )


def read_supplemental_reports(
    db_path: Path | str,
    *,
    session_id: str,
) -> SupplementalProjection:
    """Project supplemental turns without changing the completed daily occurrence."""

    path = Path(db_path)
    empty_generation = _projection_generation({"session_id": session_id, "rows": []})
    if not path.is_file():
        return SupplementalProjection(generation=empty_generation)
    try:
        with connect(path) as connection:
            session = connection.execute(
                "SELECT patient_id, status, updated_at FROM care_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise ValueError("supplemental anchor session is missing")
            child_rows = connection.execute(
                """
                SELECT session_id, status, updated_at, questionnaire_response_id
                FROM care_sessions
                WHERE parent_session_id=?
                ORDER BY created_at, session_id
                """,
                (session_id,),
            ).fetchall()
            runs = connection.execute(
                """
                SELECT r.run_id, r.session_id, r.input_text, r.output_json,
                       r.completed_at, s.status AS session_status
                FROM agent_runs r
                JOIN care_sessions s ON s.session_id=r.session_id
                WHERE s.parent_session_id=? AND r.task_id LIKE 'supplemental:%'
                ORDER BY r.completed_at, r.run_id
                """,
                (session_id,),
            ).fetchall()
            resolutions = {
                row["source_run_id"]: (row["decision"], row["resolved_at"])
                for row in connection.execute(
                    """
                    SELECT source_run_id, decision, resolved_at
                    FROM patient_supplemental_turn_resolutions
                    WHERE session_id IN (
                        SELECT session_id FROM care_sessions WHERE parent_session_id=?
                    )
                    """,
                    (session_id,),
                ).fetchall()
            }
            report_rows = connection.execute(
                """
                SELECT * FROM patient_supplemental_reports
                WHERE anchor_session_id=? ORDER BY created_at, report_id
                """,
                (session_id,),
            ).fetchall()
        unresolved = [
            row
            for row in runs
            if row["run_id"] not in resolutions
            and row["session_status"] == CareSessionStatus.IN_PROGRESS.value
        ]
        if len(unresolved) > 1:
            raise ValueError("multiple unresolved supplemental turns")
        pending = unresolved[0] if unresolved else None
        pending_result = (
            SemanticResult.model_validate(json.loads(pending["output_json"]))
            if pending is not None
            else None
        )
        reports = tuple(
            SupplementalReport(
                report_id=row["report_id"],
                patient_id=row["patient_id"],
                session_id=row["session_id"],
                anchor_session_id=row["anchor_session_id"],
                source_run_id=row["source_run_id"],
                original_text=row["original_text"],
                structured_items=tuple(json.loads(row["structured_items_json"])),
                status=row["status"],
                questionnaire_response_id=row["questionnaire_response_id"],
                observation_ids=tuple(json.loads(row["observation_ids_json"] or "[]")),
                provenance_id=row["provenance_id"],
                report_kind=row["report_kind"],
                handoff_reason_code=row["handoff_reason_code"],
                handoff_policy_version=row["handoff_policy_version"],
                created_at=row["created_at"],
                reviewed_at=row["reviewed_at"],
                review_note=row["review_note"],
            )
            for row in report_rows
        )
        if any(
            item.report_kind not in {"patient_supplemental", "semantic_handoff"}
            or (
                item.report_kind == "semantic_handoff"
                and (
                    not item.handoff_reason_code
                    or not item.handoff_policy_version
                    or item.structured_items
                    or item.questionnaire_response_id is not None
                    or item.observation_ids
                    or item.provenance_id is not None
                )
            )
            or (
                item.report_kind == "patient_supplemental"
                and (
                    item.handoff_reason_code is not None
                    or item.handoff_policy_version is not None
                )
            )
            for item in reports
        ):
            raise ValueError("supplemental report discriminator boundary mismatch")
        generation_payload = {
            "session_id": session_id,
            "session_status": session["status"],
            "session_updated_at": session["updated_at"],
            "children": [
                (
                    row["session_id"],
                    row["status"],
                    row["updated_at"],
                    row["questionnaire_response_id"],
                )
                for row in child_rows
            ],
            "runs": [(row["run_id"], row["completed_at"]) for row in runs],
            "resolutions": sorted((key, *value) for key, value in resolutions.items()),
            "reports": [
                (
                    item.report_id,
                    item.report_kind,
                    item.handoff_reason_code,
                    item.handoff_policy_version,
                    item.status,
                    item.reviewed_at,
                    item.review_note,
                )
                for item in reports
            ],
        }
        return SupplementalProjection(
            generation=_projection_generation(generation_payload),
            pending_run_id=pending["run_id"] if pending is not None else None,
            pending_text=pending["input_text"] if pending is not None else None,
            pending_items=(
                tuple(item.model_dump(mode="json") for item in pending_result.candidates)
                if pending_result is not None
                else ()
            ),
            pending_clarifications=(
                tuple(
                    item.model_dump(mode="json")
                    for item in pending_result.clarifications
                )
                if pending_result is not None
                else ()
            ),
            reports=reports,
        )
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return SupplementalProjection(
            generation=empty_generation,
            integrity_issue="补充上报记录不可安全读取",
        )


def submit_supplemental_report_turn(
    db_path: Path | str,
    *,
    session_id: str,
    expected_story_generation: str,
    expected_supplemental_generation: str,
    message_text: str,
    synthetic_confirmed: bool,
    model_adapter: MiMoSemanticAdapter | None = None,
) -> SupplementalProjection:
    """Call MiMo on a staging copy and persist only a safe, candidate-only turn."""

    text = validate_synthetic_chat_message(
        message_text, synthetic_confirmed=synthetic_confirmed
    )
    path = Path(db_path)
    before_story = read_competition_demo(path)
    before_projection = read_supplemental_reports(path, session_id=session_id)
    if (
        before_story.integrity_issue
        or before_story.session_id != session_id
        or before_story.generation != expected_story_generation
        or before_projection.integrity_issue
        or before_projection.generation != expected_supplemental_generation
        or before_projection.pending_run_id is not None
    ):
        raise CompetitionDemoConflict("补充上报状态已变化，请刷新后继续。")
    before_store = SQLiteStore(path, initialize=False)
    before_session = before_store.get_care_session(session_id)
    if before_session is None or before_session.status != CareSessionStatus.COMPLETED:
        raise CompetitionDemoConflict("请先完成今天的定时随访。")
    if before_session.parent_session_id is not None:
        raise CompetitionDemoConflict("补充上报必须锚定当天主随访。")
    before_child_ids = {
        item.session_id
        for item in before_store.list_care_sessions(before_session.patient_id)
        if item.parent_session_id == session_id
    }
    before_counts = (
        len(before_store.list_messages(before_session.patient_id)),
        len(before_store.list_observations(before_session.patient_id)),
    )
    with connect(path) as connection:
        anchor_response = connection.execute(
            "SELECT resource_json FROM fhir_questionnaire_responses WHERE resource_id=?",
            (before_session.questionnaire_response_id,),
        ).fetchone()
    if anchor_response is None:
        raise CompetitionDemoConflict("当天已完成随访缺少原始问卷记录。")
    anchor_response_sha256 = hashlib.sha256(
        anchor_response["resource_json"].encode("utf-8")
    ).hexdigest()
    child_session_id = "session-supplemental-" + uuid5(
        NAMESPACE_URL,
        f"{session_id}|{expected_supplemental_generation}|{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
    ).hex
    adapter = model_adapter or _competition_mimo_adapter()
    if not isinstance(adapter, MiMoSemanticAdapter) or not adapter.configured:
        raise CompetitionDemoStartError("豆包当前不可用；这句补充内容没有保存。")
    if (
        adapter.config.safety_llm_enabled
        or adapter.config.language_llm_enabled
        or adapter.config.summary_llm_enabled
    ):
        raise CompetitionDemoStartError("豆包补充上报配置不符合最小外发边界。")

    def loader(staging: Path):
        projection = read_supplemental_reports(staging, session_id=session_id)
        if projection.generation != expected_supplemental_generation:
            raise CompetitionDemoConflict("补充上报状态已变化。")
        store = SQLiteStore(staging, initialize=False)
        anchor = store.get_care_session(session_id)
        if anchor != before_session:
            raise CompetitionDemoConflict("当天随访锚点已变化。")
        started_at = utc_now_iso()
        child = CareSession(
            session_id=child_session_id,
            patient_id=anchor.patient_id,
            pathway_code=anchor.pathway_code,
            pathway_version=anchor.pathway_version,
            questionnaire_canonical=anchor.questionnaire_canonical,
            questionnaire_version=anchor.questionnaire_version,
            knowledge_release_id=anchor.knowledge_release_id,
            parent_session_id=anchor.session_id,
            status=CareSessionStatus.IN_PROGRESS,
            answers={},
            created_at=started_at,
            updated_at=started_at,
        )
        store.create_care_session_bundle(
            child,
            [
                AuditEvent(
                    event_id="audit-" + uuid5(
                        NAMESPACE_URL, f"{child.session_id}|supplemental_started"
                    ).hex,
                    patient_id=child.patient_id,
                    entity_type="CareSession",
                    entity_id=child.session_id,
                    event_type="supplemental_occurrence_started",
                    actor_type="synthetic_patient",
                    details_json={
                        "anchor_session_id": anchor.session_id,
                        "anchor_questionnaire_response_id": anchor.questionnaire_response_id,
                        "anchor_questionnaire_response_sha256": anchor_response_sha256,
                        "anchor_updated_at": anchor.updated_at,
                        "synthetic_only": True,
                        "clinical_assessment": "not_assessed",
                    },
                    created_at=started_at,
                )
            ],
        )
        return CareAgentService(
            store,
            care_engine=CareEngine(store),
            model_adapter=adapter,
            patient_timezone="Asia/Shanghai",
            terminology_backend=load_supplemental_terminology_backend(),
        ).analyze_supplemental(
            child.session_id,
            text,
            focus_link_ids=list(CORE_LINK_IDS),
        )

    def validator(staging: Path) -> None:
        store = SQLiteStore(staging, initialize=False)
        anchor = store.get_care_session(session_id)
        child = store.get_care_session(child_session_id)
        runs = store.list_agent_runs(child_session_id)
        story = read_competition_demo(staging)
        projection = read_supplemental_reports(staging, session_id=session_id)
        if (
            anchor != before_session
            or child is None
            or child.parent_session_id != session_id
            or child.status != CareSessionStatus.IN_PROGRESS
            or set(
                item.session_id
                for item in store.list_care_sessions(before_session.patient_id)
                if item.parent_session_id == session_id
            )
            != {*before_child_ids, child_session_id}
            or len(runs) != 1
            or story.integrity_issue
            or story.generation != expected_story_generation
            or projection.integrity_issue
            or projection.pending_run_id != runs[0].run_id
            or (
                len(store.list_messages(before_session.patient_id)),
                len(store.list_observations(before_session.patient_id)),
            )
            != before_counts
            or story.alert_count != 0
            or story.approved_clinical_rule_count != 0
        ):
            raise CompetitionDemoStartError("豆包补充上报未通过记录边界校验。")
        record = runs[0]
        result = SemanticResult.model_validate(record.output_json)
        terminology_boundary = load_supplemental_terminology_backend().catalog
        extraction = [
            item for item in result.stage_traces if item.stage == "care_extraction"
        ]
        if (
            not record.task_id.startswith(SUPPLEMENTAL_TASK_PREFIX)
            or record.model_provider not in MODEL_API_PROVIDERS
            or record.model_name != adapter.config.model_name
            or record.prompt_version != adapter.config.prompt_version
            or record.terminology_catalog_id != terminology_boundary.catalog_id
            or record.terminology_catalog_version != terminology_boundary.version
            or record.terminology_catalog_sha256
            != terminology_catalog_sha256(terminology_boundary)
            or result.mode not in MODEL_API_MODES
            or len(extraction) != 1
            or extraction[0].mode not in MODEL_API_MODES
            or result.status == SemanticStatus.BLOCKED
            or result.safety_violations
            or any(
                item.source_mode
                not in {
                    *MODEL_CANDIDATE_SOURCES,
                    CandidateSource.DETERMINISTIC_CATALOG,
                }
                for item in result.candidates
            )
            or any(
                item.link_id not in CORE_LINK_IDS
                and not item.link_id.startswith(DYNAMIC_LINK_PREFIX)
                for item in result.candidates
            )
            or any(
                item.proposed_candidate is not None
                and item.proposed_candidate.source_mode
                not in {
                    *MODEL_CANDIDATE_SOURCES,
                    CandidateSource.DETERMINISTIC_CATALOG,
                }
                for item in result.clarifications
            )
            or any(
                not _trusted_supplemental_match(
                    item.terminology_match,
                    dynamic=item.link_id.startswith(DYNAMIC_LINK_PREFIX),
                )
                for item in result.candidates
            )
            or any(
                item.proposed_candidate is not None
                and not _trusted_supplemental_match(
                    item.proposed_candidate.terminology_match,
                    dynamic=item.proposed_candidate.link_id.startswith(
                        DYNAMIC_LINK_PREFIX
                    ),
                )
                for item in result.clarifications
            )
            or any(
                option.terminology_match is not None
                and not _trusted_supplemental_match(
                    option.terminology_match,
                    dynamic=True,
                )
                for item in result.clarifications
                for option in item.options
            )
        ):
            raise CompetitionDemoStartError("豆包补充上报未通过语义安全校验。")

    try:
        _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_story_generation,
            seed_from_existing=True,
        )
    except (CompetitionDemoConflict, CompetitionDemoStartError):
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "豆包本轮补充整理未通过安全校验；原记录没有变化。"
        ) from exc
    return read_supplemental_reports(path, session_id=session_id)


def _selected_supplemental_candidates(
    store: SQLiteStore,
    *,
    run_id: str,
    result: SemanticResult,
    clarification_options: dict[str, str],
) -> tuple[list[SemanticCandidate], dict[str, tuple[str, str | None]]]:
    service = CareAgentService(
        store,
        care_engine=CareEngine(store),
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
        terminology_backend=load_supplemental_terminology_backend(),
    )
    selected = list(result.candidates)
    decisions = {
        item.candidate_id: ("accepted", None) for item in result.candidates
    }
    clarification_ids = {item.clarification_id for item in result.clarifications}
    if set(clarification_options) != clarification_ids:
        raise ValueError("请先逐项回答全部澄清问题")
    for clarification in result.clarifications:
        option_id = clarification_options[clarification.clarification_id]
        option = next(
            (
                item
                for item in clarification.options
                if item.option_id == option_id
            ),
            None,
        )
        if option is None:
            raise ValueError("澄清选项已经变化，请刷新后重试")
        candidate = service.prepare_clarification_candidate(
            run_id,
            clarification.clarification_id,
            option_id,
        )
        if candidate is not None:
            selected.append(candidate)
            action_decision = "accepted"
        elif option_id == "unsure" or option.terminology_match is None:
            action_decision = "unsure"
        else:
            action_decision = "rejected"
        decisions[clarification.clarification_id] = (action_decision, option_id)
    link_ids = [item.link_id for item in selected]
    if len(link_ids) != len(set(link_ids)):
        raise ValueError("补充上报包含重复或冲突的结构化事实")
    return selected, decisions


def _supplemental_submission_bundle(
    store: SQLiteStore,
    *,
    anchor: CareSession,
    child: CareSession,
    run_id: str,
    result: SemanticResult,
    input_text: str,
    selected: list[SemanticCandidate],
    confirmed_at: str,
) -> dict[str, Any]:
    questionnaire = CareEngine(store).questionnaire_for_session(child)
    response_id = "response-supplemental-" + uuid5(
        NAMESPACE_URL, f"{child.session_id}|{run_id}|response"
    ).hex
    report_id = "supplemental-" + uuid5(
        NAMESPACE_URL, f"{child.session_id}|{run_id}|accepted"
    ).hex
    answers: dict[str, Any] = {"free-text-report": input_text}
    answer_contexts: list[ConfirmedAnswerContext] = []
    symptom_reports: list[ConfirmedSymptomReport] = []
    temporal = result.temporal_context
    reported_at = (
        temporal.received_at_local if temporal is not None else confirmed_at
    )
    occurrence_id = (
        temporal.followup_occurrence_id
        if temporal is not None
        else f"occurrence-{child.session_id}"
    )
    for candidate in selected:
        match = candidate.terminology_match
        if match is None:
            raise ValueError("已确认补充候选缺少受控术语来源")
        effective = candidate.effective_time
        if candidate.link_id.startswith(DYNAMIC_LINK_PREFIX):
            symptom_reports.append(
                ConfirmedSymptomReport(
                    report_id="symptom-report-" + uuid5(
                        NAMESPACE_URL,
                        f"{child.session_id}|{run_id}|{candidate.candidate_id}",
                    ).hex,
                    session_id=child.session_id,
                    concept_id=match.concept_id,
                    preferred_zh=match.preferred_zh,
                    coding=match.coding.model_dump(mode="json"),
                    terminology_match=match.model_dump(mode="json"),
                    source_kind=candidate.origin.value,
                    source_run_id=run_id,
                    evidence_text=candidate.evidence_text,
                    evidence_start=candidate.evidence_start,
                    evidence_end=candidate.evidence_end,
                    followup_occurrence_id=occurrence_id,
                    patient_timezone=(
                        temporal.patient_timezone if temporal is not None else "Asia/Shanghai"
                    ),
                    reported_at=reported_at,
                    effective_start=(effective.effective_start if effective else None),
                    effective_end=(effective.effective_end if effective else None),
                    temporal_kind=(effective.kind.value if effective else None),
                    created_at=confirmed_at,
                )
            )
            continue
        answers[candidate.link_id] = candidate.answer
        answer_contexts.append(
            ConfirmedAnswerContext(
                answer_context_id="answer-context-" + uuid5(
                    NAMESPACE_URL,
                    f"{child.session_id}|{run_id}|{candidate.candidate_id}",
                ).hex,
                session_id=child.session_id,
                link_id=candidate.link_id,
                answer=candidate.answer,
                source_run_id=run_id,
                followup_occurrence_id=occurrence_id,
                patient_timezone=(
                    temporal.patient_timezone if temporal is not None else "Asia/Shanghai"
                ),
                reported_at=reported_at,
                effective_start=(effective.effective_start if effective else None),
                effective_end=(effective.effective_end if effective else None),
                temporal_kind=(effective.kind.value if effective else None),
                resolution_basis=(effective.basis.value if effective else None),
                raw_text=input_text,
                terminology_match=match.model_dump(mode="json"),
                created_at=confirmed_at,
            )
        )

    response = build_questionnaire_response(
        questionnaire=questionnaire,
        response_id=response_id,
        patient_id=child.patient_id,
        authored=confirmed_at,
        answers=answers,
        status="completed",
    )
    response["meta"] = {
        "tag": [
            {
                "system": "urn:continucare:occurrence-kind",
                "code": "supplemental-report",
                "display": "Patient supplemental report",
            }
        ]
    }
    response = validate_questionnaire_response_against_questionnaire(
        response, questionnaire
    )
    policy = CareEngine(store).observation_mapping_for_session(child)
    observations = map_response_to_observations(
        response=response,
        questionnaire=questionnaire,
        policy=policy,
        answer_contexts={item.link_id: item for item in answer_contexts},
    )
    response_text, _ = questionnaire_response_summary(response, questionnaire)
    for report in symptom_reports:
        evidence_start = response_text.find(report.evidence_text)
        if evidence_start < 0:
            raise ValueError("补充症状原话未保留在 QuestionnaireResponse 中")
        coding = CodingDefinition(**report.coding)
        effective_time = report.effective_end or report.effective_start or report.reported_at
        resource = build_patient_reported_observation(
            observation_id="observation-" + uuid5(
                NAMESPACE_URL, f"{response_id}|{report.report_id}"
            ).hex,
            patient_id=child.patient_id,
            questionnaire_response_id=response_id,
            effective_time=effective_time,
            issued_time=confirmed_at,
            code=coding,
            value_element="valueBoolean",
            value=True,
            effective_period_start=(
                report.effective_start
                if report.temporal_kind not in {None, "point_in_time"}
                else None
            ),
            effective_period_end=(
                report.effective_end
                if report.temporal_kind not in {None, "point_in_time"}
                else None
            ),
        )
        observations.append(
            Observation(
                resource=resource,
                evidence={
                    "questionnaire_response_id": response_id,
                    "confidence_tier": ConfidenceTier.PATIENT_CONFIRMED,
                    "evidence_text": report.evidence_text,
                    "evidence_start": evidence_start,
                    "evidence_end": evidence_start + len(report.evidence_text),
                    "recorded_at": confirmed_at,
                    "source_kind": report.source_kind,
                    "terminology_match": report.terminology_match,
                    "knowledge_release_id": None,
                    "observation_mapping_sha256": None,
                },
            )
        )
    admit_final_patient_report(
        patient_id=child.patient_id,
        questionnaire_response=response,
        observations=[item.as_fhir() for item in observations],
        require_observations=False,
    )
    observation_refs = [f"Observation/{item.observation_id}" for item in observations]
    provenance_id = "provenance-supplemental-" + uuid5(
        NAMESPACE_URL, f"{child.session_id}|{run_id}|provenance"
    ).hex
    provenance = build_patient_confirmation_provenance(
        target_references=[
            f"QuestionnaireResponse/{response_id}",
            *observation_refs,
        ],
        entity_source_references=[
            f"urn:continucare:agent-run:{run_id}",
            (
                "urn:continucare:terminology-boundary:"
                + terminology_catalog_sha256(
                    load_supplemental_terminology_backend().catalog
                )
            ),
        ],
        confirmed_at=confirmed_at,
        patient_id=child.patient_id,
        provenance_id=provenance_id,
    )
    completed_child = child.model_copy(
        update={
            "answers": answers,
            "status": CareSessionStatus.COMPLETED,
            "questionnaire_response_id": response_id,
            "updated_at": confirmed_at,
            "completed_at": confirmed_at,
        }
    )
    message = FollowUpMessage(
        message_id=response_id,
        patient_id=child.patient_id,
        message_text=response_text,
        submitted_at=confirmed_at,
        source="patient_supplemental_report",
        processing_status="structured_complete",
    )
    return {
        "report_id": report_id,
        "session": completed_child,
        "message": message,
        "questionnaire": questionnaire,
        "response": response,
        "observations": observations,
        "answer_contexts": answer_contexts,
        "symptom_reports": symptom_reports,
        "provenance": provenance,
        "structured_items": [item.model_dump(mode="json") for item in selected],
    }


def resolve_supplemental_turn(
    db_path: Path | str,
    *,
    session_id: str,
    run_id: str,
    decision: str,
    expected_story_generation: str,
    expected_supplemental_generation: str,
    clarification_options: dict[str, str] | None = None,
) -> SupplementalProjection:
    """Atomically complete or discard one independent supplemental occurrence."""

    if decision not in {"accepted", "rejected"}:
        raise ValueError("unsupported supplemental decision")
    path = Path(db_path)
    clarification_options = dict(clarification_options or {})
    now = utc_now_iso()
    with demo_write_guard(path, expected_generation=expected_story_generation):
        current_projection = read_supplemental_reports(path, session_id=session_id)
        with connect(path) as replay_connection:
            existing_resolution = replay_connection.execute(
                """
                SELECT decision, session_id FROM patient_supplemental_turn_resolutions
                WHERE source_run_id=?
                """,
                (run_id,),
            ).fetchone()
            existing_options = {
                row["action_id"]: row["option_id"]
                for row in replay_connection.execute(
                    """
                    SELECT action_id, option_id FROM conversation_action_resolutions
                    WHERE source_run_id=? AND option_id IS NOT NULL
                    """,
                    (run_id,),
                ).fetchall()
            }
        if existing_resolution is not None:
            if (
                existing_resolution["decision"] == decision
                and existing_options == clarification_options
            ):
                return current_projection
            raise CompetitionDemoConflict("这句补充上报已经以其他选择处理。")
        if (
            current_projection.integrity_issue
            or current_projection.generation != expected_supplemental_generation
            or current_projection.pending_run_id != run_id
        ):
            raise CompetitionDemoConflict("补充上报状态已变化，请刷新后重试。")
        store = SQLiteStore(path, initialize=False)
        anchor_model = store.get_care_session(session_id)
        record = store.get_agent_run(run_id)
        child = store.get_care_session(record.session_id) if record is not None else None
        if (
            anchor_model is None
            or child is None
            or record is None
            or record.session_id != child.session_id
            or child.parent_session_id != session_id
        ):
            raise ValueError("补充上报 occurrence 与模型记录不一致")
        result = SemanticResult.model_validate(record.output_json)
        if decision == "accepted":
            selected, action_decisions = _selected_supplemental_candidates(
                store,
                run_id=run_id,
                result=result,
                clarification_options=clarification_options,
            )
            bundle = _supplemental_submission_bundle(
                store,
                anchor=anchor_model,
                child=child,
                run_id=run_id,
                result=result,
                input_text=record.input_text,
                selected=selected,
                confirmed_at=now,
            )
        else:
            action_decisions = {
                **{item.candidate_id: ("rejected", None) for item in result.candidates},
                **{
                    item.clarification_id: ("rejected", None)
                    for item in result.clarifications
                },
            }
            bundle = None
        with connect(path) as connection:
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("BEGIN IMMEDIATE")
            anchor = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            child_row = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id=?", (child.session_id,)
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            patient = (
                connection.execute(
                    "SELECT synthetic FROM patients WHERE patient_id=?",
                    (anchor["patient_id"],),
                ).fetchone()
                if anchor is not None
                else None
            )
            if (
                anchor is None
                or anchor["status"] != CareSessionStatus.COMPLETED.value
                or child_row is None
                or child_row["status"] != CareSessionStatus.IN_PROGRESS.value
                or child_row["parent_session_id"] != session_id
                or child_row["updated_at"] != child.updated_at
                or child_row["questionnaire_response_id"] is not None
                or json.loads(child_row["answers_json"]) != {}
                or run is None
                or run["session_id"] != child.session_id
                or not run["task_id"].startswith(SUPPLEMENTAL_TASK_PREFIX)
                or patient is None
                or patient["synthetic"] != 1
            ):
                raise ValueError("补充上报与已完成的合成随访不匹配")
            if connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE patient_id=?",
                (anchor["patient_id"],),
            ).fetchone()[0] != 0:
                raise ValueError("存在未授权 Alert，补充上报已停止")
            if connection.execute(
                """
                SELECT COUNT(*) FROM layer4_contract_records
                WHERE record_type='clinical_rule' AND status IN ('approved', 'active')
                """
            ).fetchone()[0] != 0:
                raise ValueError("存在获批临床规则，冻结演示边界已停止")
            start_audit = connection.execute(
                """
                SELECT details_json FROM audit_events
                WHERE entity_type='CareSession' AND entity_id=?
                  AND event_type='supplemental_occurrence_started'
                """,
                (child.session_id,),
            ).fetchone()
            anchor_response = connection.execute(
                "SELECT resource_json FROM fhir_questionnaire_responses WHERE resource_id=?",
                (anchor["questionnaire_response_id"],),
            ).fetchone()
            if start_audit is None or anchor_response is None:
                raise ValueError("补充上报缺少不可变日随访锚点")
            anchor_details = json.loads(start_audit["details_json"])
            if (
                anchor_details.get("anchor_session_id") != session_id
                or anchor_details.get("anchor_updated_at") != anchor["updated_at"]
                or anchor_details.get("anchor_questionnaire_response_id")
                != anchor["questionnaire_response_id"]
                or anchor_details.get("anchor_questionnaire_response_sha256")
                != hashlib.sha256(
                    anchor_response["resource_json"].encode("utf-8")
                ).hexdigest()
            ):
                raise ValueError("当天随访锚点已变化，补充上报停止")
            if connection.execute(
                "SELECT 1 FROM patient_supplemental_turn_resolutions WHERE source_run_id=?",
                (run_id,),
            ).fetchone() is not None:
                raise CompetitionDemoConflict("这句补充上报已处理。")
            stored_result = SemanticResult.model_validate(json.loads(run["output_json"]))
            terminology_boundary = load_supplemental_terminology_backend().catalog
            extraction = [
                item
                for item in result.stage_traces
                if item.stage == "care_extraction"
            ]
            if (
                stored_result != result
                or result.mode not in MODEL_API_MODES
                or len(extraction) != 1
                or extraction[0].mode not in MODEL_API_MODES
                or run["model_provider"] not in MODEL_API_PROVIDERS
                or not run["model_name"]
                or run["model_name"] != extraction[0].model_name
                or run["prompt_version"] != extraction[0].prompt_version
                or run["terminology_catalog_id"]
                != terminology_boundary.catalog_id
                or run["terminology_catalog_version"]
                != terminology_boundary.version
                or run["terminology_catalog_sha256"]
                != terminology_catalog_sha256(terminology_boundary)
                or result.status == SemanticStatus.BLOCKED
                or result.safety_violations
            ):
                raise ValueError("模型补充上报来源不可信")
            action_ids = {
                *[item.candidate_id for item in result.candidates],
                *[item.clarification_id for item in result.clarifications],
            }
            if set(action_decisions) != action_ids:
                raise ValueError("补充上报仍有未决候选或澄清")
            existing_action = connection.execute(
                "SELECT 1 FROM conversation_action_resolutions WHERE source_run_id=?",
                (run_id,),
            ).fetchone()
            if existing_action is not None:
                raise CompetitionDemoConflict("补充候选已被其他页面处理。")

            if decision == "accepted":
                assert bundle is not None
                response = validate_questionnaire_response_against_questionnaire(
                    bundle["response"], bundle["questionnaire"]
                )
                provenance = validate_layer4_fhir_resource(
                    bundle["provenance"], expected_resource_type="Provenance"
                )
                if any(
                    (
                        connection.execute(
                            "SELECT COUNT(*) FROM fhir_questionnaire_responses WHERE resource_id=?",
                            (response["id"],),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM fhir_observations WHERE questionnaire_response_id=?",
                            (response["id"],),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM patient_supplemental_reports WHERE source_run_id=?",
                            (run_id,),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM followup_messages WHERE message_id=?",
                            (response["id"],),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM confirmed_answer_contexts WHERE session_id=?",
                            (child.session_id,),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM confirmed_symptom_reports WHERE session_id=?",
                            (child.session_id,),
                        ).fetchone()[0],
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM layer4_fhir_resources
                            WHERE resource_type='Provenance' AND resource_id=?
                            """,
                            (provenance["id"],),
                        ).fetchone()[0],
                    )
                ):
                    raise ValueError("补充 occurrence 已存在部分写入，已停止")
                for context in bundle["answer_contexts"]:
                    connection.execute(
                        """
                        INSERT INTO confirmed_answer_contexts (
                            answer_context_id, session_id, link_id, answer_json,
                            source_run_id, followup_occurrence_id, patient_timezone,
                            reported_at, effective_start, effective_end, temporal_kind,
                            resolution_basis, raw_text, terminology_match_json,
                            status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            context.answer_context_id,
                            context.session_id,
                            context.link_id,
                            _canonical(context.answer),
                            context.source_run_id,
                            context.followup_occurrence_id,
                            context.patient_timezone,
                            context.reported_at,
                            context.effective_start,
                            context.effective_end,
                            context.temporal_kind,
                            context.resolution_basis,
                            context.raw_text,
                            _canonical(context.terminology_match),
                            context.status,
                            context.created_at,
                        ),
                    )
                for symptom in bundle["symptom_reports"]:
                    connection.execute(
                        """
                        INSERT INTO confirmed_symptom_reports (
                            report_id, session_id, concept_id, preferred_zh, coding_json,
                            terminology_match_json, source_kind, source_run_id,
                            evidence_text, evidence_start, evidence_end,
                            followup_occurrence_id, patient_timezone, reported_at,
                            effective_start, effective_end, temporal_kind, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symptom.report_id,
                            symptom.session_id,
                            symptom.concept_id,
                            symptom.preferred_zh,
                            _canonical(symptom.coding),
                            _canonical(symptom.terminology_match),
                            symptom.source_kind,
                            symptom.source_run_id,
                            symptom.evidence_text,
                            symptom.evidence_start,
                            symptom.evidence_end,
                            symptom.followup_occurrence_id,
                            symptom.patient_timezone,
                            symptom.reported_at,
                            symptom.effective_start,
                            symptom.effective_end,
                            symptom.temporal_kind,
                            symptom.status,
                            symptom.created_at,
                        ),
                    )
                message = bundle["message"]
                connection.execute(
                    """
                    INSERT INTO followup_messages (
                        message_id, patient_id, message_text, submitted_at,
                        source, processing_status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    tuple(message.model_dump().values()),
                )
                connection.execute(
                    """
                    INSERT INTO fhir_questionnaire_responses (
                        resource_id, patient_id, message_id, resource_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        response["id"],
                        child.patient_id,
                        response["id"],
                        _canonical(response),
                        response["authored"],
                    ),
                )
                for observation in bundle["observations"]:
                    connection.execute(
                        """
                        INSERT INTO fhir_observations (
                            observation_id, patient_id, questionnaire_response_id,
                            effective_time, resource_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observation.observation_id,
                            observation.patient_id,
                            observation.message_id,
                            observation.effective_time,
                            _canonical(observation.as_fhir()),
                            observation.created_at,
                        ),
                    )
                    evidence = observation.evidence
                    connection.execute(
                        """
                        INSERT INTO observation_evidence (
                            observation_id, confidence_tier, evidence_text,
                            evidence_start, evidence_end, recorded_at, source_kind,
                            terminology_match_json, metric_id, evidence_claim_ids_json,
                            knowledge_release_id, observation_mapping_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observation.observation_id,
                            evidence.confidence_tier.value,
                            evidence.evidence_text,
                            evidence.evidence_start,
                            evidence.evidence_end,
                            evidence.recorded_at,
                            evidence.source_kind,
                            _canonical(evidence.terminology_match)
                            if evidence.terminology_match is not None
                            else None,
                            evidence.metric_id,
                            _canonical(evidence.evidence_claim_ids),
                            evidence.knowledge_release_id,
                            evidence.observation_mapping_sha256,
                        ),
                    )
                completed = bundle["session"]
                cursor = connection.execute(
                    """
                    UPDATE care_sessions SET answers_json=?, status='completed',
                        questionnaire_response_id=?, updated_at=?, completed_at=?
                    WHERE session_id=? AND status='in_progress' AND updated_at=?
                    """,
                    (
                        _canonical(completed.answers),
                        completed.questionnaire_response_id,
                        completed.updated_at,
                        completed.completed_at,
                        completed.session_id,
                        child.updated_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CompetitionDemoConflict("补充 occurrence 已变化。")
                meta = provenance["meta"]
                connection.execute(
                    """
                    INSERT INTO layer4_fhir_resources (
                        resource_type, resource_id, version_id, patient_id, status,
                        clinical_time, resource_json, is_current, created_at, updated_at
                    ) VALUES ('Provenance', ?, ?, ?, NULL, ?, ?, 1, ?, ?)
                    """,
                    (
                        provenance["id"],
                        meta["versionId"],
                        child.patient_id,
                        provenance["recorded"],
                        _canonical(provenance),
                        meta["lastUpdated"],
                        meta["lastUpdated"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO patient_supplemental_reports (
                        report_id, patient_id, session_id, anchor_session_id,
                        source_run_id, original_text, structured_items_json,
                        questionnaire_response_id, observation_ids_json,
                        provenance_id, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?)
                    """,
                    (
                        bundle["report_id"],
                        child.patient_id,
                        child.session_id,
                        session_id,
                        run_id,
                        record.input_text,
                        _canonical(bundle["structured_items"]),
                        response["id"],
                        _canonical(
                            [item.observation_id for item in bundle["observations"]]
                        ),
                        provenance["id"],
                        now,
                    ),
                )
                report_id = bundle["report_id"]
            else:
                cursor = connection.execute(
                    """
                    UPDATE care_sessions SET status='stopped', updated_at=?, completed_at=?
                    WHERE session_id=? AND status='in_progress' AND updated_at=?
                    """,
                    (now, now, child.session_id, child.updated_at),
                )
                if cursor.rowcount != 1:
                    raise CompetitionDemoConflict("补充 occurrence 已变化。")
                report_id = None

            connection.execute(
                """
                INSERT INTO patient_supplemental_turn_resolutions (
                    source_run_id, session_id, decision, resolved_at
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, child.session_id, decision, now),
            )
            for action_id, (action_decision, option_id) in action_decisions.items():
                connection.execute(
                    """
                    INSERT INTO conversation_action_resolutions (
                        action_id, source_run_id, session_id, response_run_id,
                        decision, option_id, response_text, resolved_at
                    ) VALUES (?, ?, ?, NULL, ?, ?, NULL, ?)
                    """,
                    (
                        action_id,
                        run_id,
                        child.session_id,
                        action_decision,
                        option_id,
                        now,
                    ),
                )
            event_type = (
                "supplemental_patient_report_confirmed"
                if decision == "accepted"
                else "supplemental_patient_report_discarded"
            )
            event_id = "audit-" + uuid5(
                NAMESPACE_URL, f"{run_id}|{event_type}"
            ).hex
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, patient_id, entity_type, entity_id,
                    event_type, actor_type, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'synthetic_patient', ?, ?)
                """,
                (
                    event_id,
                    anchor["patient_id"],
                    "SupplementalReport" if report_id else "AgentRun",
                    report_id or run_id,
                    event_type,
                    _canonical(
                        {
                            "session_id": child.session_id,
                            "anchor_session_id": session_id,
                            "source_run_id": run_id,
                            "decision": decision,
                            "structured_link_ids": [
                                item.link_id
                                for item in (selected if decision == "accepted" else [])
                            ],
                            "manual_review_required": decision == "accepted",
                            "questionnaire_response_id": (
                                bundle["response"]["id"] if bundle else None
                            ),
                            "observation_ids": (
                                [
                                    item.observation_id
                                    for item in bundle["observations"]
                                ]
                                if bundle
                                else []
                            ),
                            "clinical_assessment": "not_assessed",
                            "alert_created": False,
                        }
                    ),
                    now,
                ),
            )
            if bundle is not None:
                for entity_type, entity_id, event_type, details in (
                    (
                        "CareSession",
                        child.session_id,
                        "supplemental_occurrence_completed",
                        {
                            "anchor_session_id": session_id,
                            "questionnaire_response_id": bundle["response"]["id"],
                            "observation_ids": [
                                item.observation_id
                                for item in bundle["observations"]
                            ],
                            "clinical_assessment": "not_assessed",
                        },
                    ),
                    (
                        "QuestionnaireResponse",
                        bundle["response"]["id"],
                        "supplemental_questionnaire_response_completed",
                        {
                            "session_id": child.session_id,
                            "anchor_session_id": session_id,
                            "observation_ids": [
                                item.observation_id
                                for item in bundle["observations"]
                            ],
                            "occurrence_kind": "supplemental-report",
                        },
                    ),
                ):
                    audit_id = "audit-" + uuid5(
                        NAMESPACE_URL, f"{run_id}|{event_type}"
                    ).hex
                    connection.execute(
                        """
                        INSERT INTO audit_events (
                            event_id, patient_id, entity_type, entity_id,
                            event_type, actor_type, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'deterministic_workflow', ?, ?)
                        """,
                        (
                            audit_id,
                            anchor["patient_id"],
                            entity_type,
                            entity_id,
                            event_type,
                            _canonical(details),
                            now,
                        ),
                    )
    return read_supplemental_reports(path, session_id=session_id)


def review_supplemental_report(
    db_path: Path | str,
    *,
    session_id: str,
    report_id: str,
    expected_story_generation: str,
    expected_supplemental_generation: str,
    note: str,
) -> SupplementalProjection:
    """Record a synthetic nurse's read-only review without clinical assessment."""

    note_text = note.strip()
    if not note_text:
        raise ValueError("请填写人工复核说明")
    if len(note_text) > 1000:
        raise ValueError("人工复核说明过长")
    path = Path(db_path)
    now = utc_now_iso()
    with demo_write_guard(path, expected_generation=expected_story_generation):
        projection = read_supplemental_reports(path, session_id=session_id)
        if (
            projection.integrity_issue
            or projection.generation != expected_supplemental_generation
        ):
            raise CompetitionDemoConflict("补充上报队列已变化，请刷新。")
        with connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM patient_supplemental_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if (
                row is None
                or row["anchor_session_id"] != session_id
                or row["status"] != "requested"
            ):
                raise ValueError("补充上报已被处理或不存在")
            connection.execute(
                """
                UPDATE patient_supplemental_reports
                SET status='reviewed', reviewed_at=?, review_note=?
                WHERE report_id=? AND status='requested'
                """,
                (now, note_text, report_id),
            )
            event_id = "audit-" + uuid5(
                NAMESPACE_URL, f"{report_id}|supplemental_patient_report_reviewed"
            ).hex
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, patient_id, entity_type, entity_id,
                    event_type, actor_type, details_json, created_at
                ) VALUES (?, ?, 'SupplementalReport', ?,
                          'supplemental_patient_report_reviewed',
                          'synthetic_nurse', ?, ?)
                """,
                (
                    event_id,
                    row["patient_id"],
                    report_id,
                    _canonical(
                        {
                            "session_id": row["session_id"],
                            "anchor_session_id": session_id,
                            "source_run_id": row["source_run_id"],
                            "note": note_text,
                            "clinical_assessment": "not_assessed",
                            "external_send": "disabled",
                        }
                    ),
                    now,
                ),
            )
    return read_supplemental_reports(path, session_id=session_id)
