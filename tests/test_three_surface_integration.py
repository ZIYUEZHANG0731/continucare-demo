from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import continucare.doctor_planning as doctor_planning
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import (
    SemanticModelConfig,
    UnconfiguredModelAdapter,
)
from continucare.care_engine import CareEngine
from continucare.db import reset_demo
from continucare.doctor_planning import (
    build_followup_plan_proposal,
    confirm_followup_plan,
)
from continucare.doctor_portal import DoctorPortalBoundaryError, build_doctor_portal_state
from continucare.nurse_portal import (
    CHECKLIST_ITEMS,
    acknowledge_nurse_task_command,
    build_nurse_portal_state,
    record_nurse_outcome_command,
    start_nurse_task_command,
)
from continucare.patient_mobile import (
    _filter_collection_to_questionnaire,
    build_patient_mobile_state,
)
from continucare.services.competition_demo import (
    demo_write_guard,
    read_competition_demo,
    submit_activated_plan_feedback,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.patient_checkin import resolve_patient_chat_focus


def test_doctor_patient_nurse_doctor_share_one_workflow(tmp_path, monkeypatch):
    db_path = tmp_path / "three-surfaces.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)

    proposal = build_followup_plan_proposal()
    saved = confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [
                {
                    "metricId": item["metricId"],
                    "frequency": item["defaultFrequency"],
                }
                for item in proposal["candidates"]
            ],
        }
    )

    patient_state = build_patient_mobile_state()
    assert patient_state["kind"] == "collecting"
    assert saved["activationSessionId"] == read_competition_demo(db_path).session_id

    activated = read_competition_demo(db_path)
    candidate = submit_activated_plan_feedback(
        db_path,
        expected_generation=activated.generation,
        use_mimo=False,
    )
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    confirmed = ConfirmedReviewService(store, care_agent=agent, care_engine=engine)
    record = store.get_agent_run(candidate.run_id)
    candidate_ids = [item["candidate_id"] for item in record.output_json["candidates"]]
    with demo_write_guard(db_path, expected_generation=candidate.generation):
        confirmed.accept_all(candidate.run_id, candidate_ids)

    nurse_state = build_nurse_portal_state()
    task_id = nurse_state["selectedTaskId"]
    acknowledge_nurse_task_command(
        {"generation": nurse_state["generation"], "taskId": task_id}
    )
    nurse_state = build_nurse_portal_state(selected_task_id=task_id)
    start_nurse_task_command(
        {"generation": nurse_state["generation"], "taskId": task_id}
    )
    nurse_state = build_nurse_portal_state(selected_task_id=task_id)
    record_nurse_outcome_command(
        {
            "generation": nurse_state["generation"],
            "taskId": task_id,
            "outcome": "escalated_to_doctor",
            "note": "护士已核对患者确认记录，请医生进一步评估。",
            "checklist": [item_id for item_id, _ in CHECKLIST_ITEMS],
        }
    )

    doctor_state = build_doctor_portal_state()
    assert doctor_state["collaboration"]["pendingCount"] == 1
    escalation = doctor_state["collaboration"]["escalations"][0]
    assert escalation["taskId"] == task_id
    assert escalation["nurseNote"] == "护士已核对患者确认记录，请医生进一步评估。"
    assert escalation["clinicalAssessment"] == "not_assessed"
    assert escalation["answers"]
    assert build_followup_plan_proposal()["currentPlan"]["planId"] == saved["planId"]


def test_patient_questions_follow_the_confirmed_collectable_plan(tmp_path, monkeypatch):
    db_path = tmp_path / "selected-plan.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)
    proposal = build_followup_plan_proposal()
    by_id = {item["metricId"]: item for item in proposal["candidates"]}

    saved = confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [
                {"metricId": "body_weight", "frequency": "weekly"},
                {
                    "metricId": "abdominal_pain_present_now",
                    "frequency": by_id["abdominal_pain_present_now"]["defaultFrequency"],
                },
            ],
        }
    )

    patient_state = build_patient_mobile_state()
    assert saved["patientQuestionMetricIds"] == [
        "body_weight",
        "abdominal_pain_present_now",
    ]
    assert saved["recordPointCount"] == 2
    assert {item["recordPointId"] for item in saved["recordPoints"]} == {
        "body-weight",
        "abdominal-pain",
    }
    assert patient_state["nextLinkId"] == "body-weight"
    assert patient_state["activePlan"]["linkIds"] == (
        "body-weight",
        "abdominal-pain-present",
    )


def test_weight_only_doctor_plan_uses_patient_conversation(tmp_path, monkeypatch):
    db_path = tmp_path / "unsupported-plan.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)
    proposal = build_followup_plan_proposal()

    saved = confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [{"metricId": "body_weight", "frequency": "weekly"}],
        }
    )

    assert saved["recordPointCount"] == 1
    assert saved["patientQuestionMetricIds"] == ["body_weight"]
    patient_state = build_patient_mobile_state()
    assert patient_state["kind"] == "collecting"
    assert patient_state["nextLinkId"] == "body-weight"
    assert patient_state["allowedActions"] == ["chat"]
    assert patient_state["activePlan"]["confirmedRecordPointCount"] == 1


def test_old_questionnaire_filters_newer_weight_task_from_patient_page():
    collection = {
        "linkIds": ("body-weight",),
        "patientQuestionMetricIds": ("body_weight",),
        "recordPoints": [
            {
                "recordPointId": "body-weight",
                "linkIds": ["body-weight"],
                "metricIds": ["body_weight"],
            }
        ],
        "confirmedRecordPointCount": 1,
    }

    filtered = _filter_collection_to_questionnaire(collection, ())

    assert filtered["linkIds"] == ()
    assert filtered["patientQuestionMetricIds"] == ()
    assert filtered["recordPoints"] == []
    assert filtered["confirmedRecordPointCount"] == 1


def test_concurrent_stale_plan_confirmations_cannot_both_commit(tmp_path, monkeypatch):
    db_path = tmp_path / "concurrent-plans.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)
    initial = build_followup_plan_proposal()
    confirm_followup_plan(
        {
            "patientId": initial["patientId"],
            "proposalId": initial["proposalId"],
            "startDate": initial["period"]["startDate"],
            "endDate": initial["period"]["endDate"],
            "items": [
                {"metricId": item["metricId"], "frequency": item["defaultFrequency"]}
                for item in initial["candidates"]
            ],
        }
    )
    proposal = build_followup_plan_proposal()
    payload = {
        "patientId": proposal["patientId"],
        "proposalId": proposal["proposalId"],
        "startDate": proposal["period"]["startDate"],
        "endDate": proposal["period"]["endDate"],
        "items": [
            {"metricId": item["metricId"], "frequency": item["defaultFrequency"]}
            for item in proposal["candidates"]
        ],
    }
    barrier = Barrier(2)
    real_persist = doctor_planning.persist_doctor_plan_with_activation

    def synchronized_persist(*args, **kwargs):
        barrier.wait(timeout=5)
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(
        doctor_planning,
        "persist_doctor_plan_with_activation",
        synchronized_persist,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(confirm_followup_plan, payload) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as exc:  # asserted by type below
                outcomes.append(exc)

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, doctor_planning.CompetitionDemoConflict) for item in outcomes) == 1
    assert build_followup_plan_proposal()["currentPlan"]["planVersion"] == 2


def test_final_submission_rejects_structure_outside_current_plan(tmp_path, monkeypatch):
    db_path = tmp_path / "out-of-plan-answer.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)
    proposal = build_followup_plan_proposal()
    confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [
                {"metricId": "body_weight", "frequency": "weekly"},
                {"metricId": "abdominal_pain_present_now", "frequency": "daily"},
            ],
        }
    )
    progress = read_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    session = store.get_care_session(progress.session_id)
    with pytest.raises(ValueError, match="不在本轮医生方案"):
        resolve_patient_chat_focus(
            session,
            message_text="我现在有恶心",
            default_link_id="abdominal-pain-present",
            active_contexts=(),
            run_ids_newest_first=(),
            collection_link_ids=("abdominal-pain-present",),
        )
    store.update_care_session(
        session.session_id,
        answers={
            "abdominal-pain-present": False,
            "nausea-present": True,
            "free-text-report": "今天没有腹痛，但额外写入了计划外恶心字段。",
        },
        status=session.status,
        updated_at=session.updated_at,
    )
    engine = CareEngine(store)
    service = ConfirmedReviewService(
        store,
        care_agent=CareAgentService(
            store,
            care_engine=engine,
            model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
            patient_timezone="Asia/Shanghai",
        ),
        care_engine=engine,
    )

    with pytest.raises(ValueError, match="不在当前医生方案"):
        service.submit_confirmed_draft(session.session_id)
