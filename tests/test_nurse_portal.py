from __future__ import annotations

import json
from pathlib import Path

import pytest

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.layer4.manual_reviews import ManualReviewQueue
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.nurse_portal import (
    CHECKLIST_ITEMS,
    NursePortalBoundaryError,
    acknowledge_nurse_task_command,
    build_nurse_portal_state,
    record_nurse_outcome_command,
    start_nurse_task_command,
)
from continucare.services.competition_demo import (
    demo_write_guard,
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService


ROOT = Path(__file__).parents[1]


def _seed_requested_task(db_path) -> None:
    progress = start_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    confirmed = ConfirmedReviewService(store, care_agent=agent, care_engine=engine)
    record = store.get_agent_run(progress.run_id)
    candidate_ids = [item["candidate_id"] for item in record.output_json["candidates"]]
    with demo_write_guard(db_path, expected_generation=progress.generation):
        confirmed.accept_all(progress.run_id, candidate_ids)


def test_nurse_portal_projects_chinese_workbench_without_clinical_codes(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "nurse-portal.db"
    _seed_requested_task(db_path)
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))

    state = build_nurse_portal_state()

    assert state["kind"] == "ready"
    assert state["counts"] == {"pending": 1, "completed": 0}
    assert state["selectedTask"]["primaryAction"] == "acknowledge"
    answers = state["selectedTask"]["answers"]
    assert any(item["question"] == "现在有恶心吗？" for item in answers)
    assert all(set(item) == {"question", "answer", "wide"} for item in answers)
    rendered = json.dumps(state, ensure_ascii=False)
    for prohibited in (
        "QuestionnaireResponse/",
        "Observation/",
        "value[x]",
        "linkId",
        "SNOMED",
        "FHIR",
    ):
        assert prohibited not in rendered


def test_nurse_portal_requires_complete_checklist_before_human_escalation(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "nurse-portal-command.db"
    _seed_requested_task(db_path)
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    requested = build_nurse_portal_state()
    task_id = requested["selectedTaskId"]

    acknowledge_nurse_task_command(
        {"generation": requested["generation"], "taskId": task_id}
    )
    received = build_nurse_portal_state(selected_task_id=task_id)
    assert received["selectedTask"]["primaryAction"] == "start"

    start_nurse_task_command(
        {"generation": received["generation"], "taskId": task_id}
    )
    in_progress = build_nurse_portal_state(selected_task_id=task_id)
    assert in_progress["selectedTask"]["primaryAction"] == "record_outcome"

    with pytest.raises(NursePortalBoundaryError, match="逐项完成"):
        record_nurse_outcome_command(
            {
                "generation": in_progress["generation"],
                "taskId": task_id,
                "outcome": "escalated_to_doctor",
                "note": "护士人工判断需要医生查看。",
                "checklist": [CHECKLIST_ITEMS[0][0]],
            }
        )

    record_nurse_outcome_command(
        {
            "generation": in_progress["generation"],
            "taskId": task_id,
            "outcome": "escalated_to_doctor",
            "note": "护士已完成人工复核，请医生进行临床评估。",
            "checklist": [item_id for item_id, _ in CHECKLIST_ITEMS],
        }
    )
    completed = build_nurse_portal_state(selected_task_id=task_id)
    assert completed["selectedTask"]["outcomeLabel"] == "上报医生评估"
    assert completed["selectedTask"]["reviewNote"] == (
        "护士已完成人工复核，请医生进行临床评估。"
    )
    assert SQLiteStore(db_path, initialize=False).list_alerts(DEMO_PATIENT_ID) == []
    tasks = ManualReviewQueue(
        Layer4SQLiteStore(db_path, initialize=False)
    ).list_for_patient(DEMO_PATIENT_ID)
    assert tasks[0]["statusReason"]["coding"][0]["code"] == (
        "human-escalated-to-doctor"
    )


def test_nurse_frontend_is_a_real_react_route_and_not_streamlit_markup():
    main_source = (ROOT / "patient-web" / "src" / "main.jsx").read_text("utf-8")
    nurse_source = (ROOT / "patient-web" / "src" / "nurse.jsx").read_text("utf-8")
    styles = (ROOT / "patient-web" / "src" / "nurse.css").read_text("utf-8")

    assert 'window.location.pathname.startsWith("/nurse")' in main_source
    assert "loadNurseState" in nurse_source
    assert "/api/nurse/tasks/outcome" in nurse_source
    assert "保存护士人工决定" in nurse_source
    assert "streamlit" not in nurse_source.lower()
    assert ".nurse-sidebar" in styles
    assert "@media (max-width: 900px)" in styles
