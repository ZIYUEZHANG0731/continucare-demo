from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.db import initialize_database, reset_demo
from continucare.product_mvp import (
    ProductRole,
    build_operations_snapshot,
    build_product_context,
)
from continucare.services.competition_demo import (
    CompetitionDemoProgress,
    CompetitionDemoStage,
    activate_competition_plan,
    demo_write_guard,
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService


ROOT = Path(__file__).parents[1]
OPERATIONS_PAGE = ROOT / "pages" / "6_operations.py"
PATIENT_PAGE = ROOT / "pages" / "1_patient_followup.py"
NURSE_PAGE = ROOT / "pages" / "2_nurse_risk_center.py"
DOCTOR_PAGE = ROOT / "pages" / "3_doctor_summary.py"


def test_three_role_pages_share_doctor_first_workflow(monkeypatch, tmp_path):
    db_path = tmp_path / "three-role.db"
    reset_demo(db_path)
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)

    doctor = AppTest.from_file(str(DOCTOR_PAGE), default_timeout=10).run()
    assert not doctor.exception
    assert {item.label for item in doctor.button} == {
        "刷新共享状态",
        "确认并启动随访方案",
    }
    next(
        item for item in doctor.button if item.label == "确认并启动随访方案"
    ).click().run()
    assert not doctor.exception
    activated = read_competition_demo(db_path)
    assert activated.stage == CompetitionDemoStage.PLAN_ACTIVATED

    patient = AppTest.from_file(str(PATIENT_PAGE), default_timeout=10).run()
    assert not patient.exception
    assert {item.label for item in patient.button} == {"刷新共享状态"}
    assert all("MiMo" not in item.label for item in patient.button)
    assert all("离线" not in item.label for item in patient.button)
    assert len(patient.chat_input) == 1
    assert patient.chat_input[0].disabled is True
    assert any("今天感觉怎么样" in item.value for item in patient.markdown)

    nurse = AppTest.from_file(str(NURSE_PAGE), default_timeout=10).run()
    assert not nurse.exception
    assert any("等待患者提交并确认" in item.value for item in nurse.info)

    assert read_competition_demo(db_path).stage == CompetitionDemoStage.PLAN_ACTIVATED


def test_completed_patient_page_keeps_supplemental_input_and_offers_restart(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "completed-patient.db"
    start_competition_demo(db_path)
    progress = read_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    review = ConfirmedReviewService(store, care_agent=agent, care_engine=engine)
    record = store.get_agent_run(progress.run_id)
    candidate_ids = [item["candidate_id"] for item in record.output_json["candidates"]]
    with demo_write_guard(db_path, expected_generation=progress.generation):
        review.accept_all(progress.run_id, candidate_ids)

    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)
    app = AppTest.from_file(str(PATIENT_PAGE), default_timeout=10).run()

    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.chat_input[0].placeholder == "输入一条合成补充上报"
    assert any("已有记录不会被后续对话改写" in item.value for item in app.success)
    assert any("随时补充上报" in item.value for item in app.markdown)
    assert any(item.label == "我知道当前这轮合成演示记录会被清空" for item in app.checkbox)
    restart = next(
        item for item in app.button if item.label == "清空旧演示并去医生端启动新一轮"
    )
    assert restart.disabled is True
    next(
        item
        for item in app.checkbox
        if item.label == "我知道当前这轮合成演示记录会被清空"
    ).check().run()
    restart = next(
        item for item in app.button if item.label == "清空旧演示并去医生端启动新一轮"
    )
    assert restart.disabled is False


def test_product_context_is_explicitly_role_scoped_and_synthetic(tmp_path):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)
    store = SQLiteStore(db_path, initialize=False)

    context = build_product_context(store, ProductRole.DOCTOR)

    assert context.role_label == "医生端 · 复诊工作台"
    assert context.patient is not None
    assert context.patient.synthetic is True
    assert context.synthetic_only is True
    assert context.role_simulation is True


def test_product_context_rejects_non_synthetic_patient(tmp_path):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)
    store = SQLiteStore(db_path, initialize=False)
    from continucare.db import connect

    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO patients (
                patient_id, display_name, synthetic, pathway_code,
                enrollment_date, next_visit_date, status, created_at
            ) VALUES ('P-REAL', '不应进入体验的患者', 0, 'GLP1-14D',
                      '2026-08-01', '2026-08-15', 'active',
                      '2026-08-01T00:00:00+00:00')
            """
        )

    with pytest.raises(ValueError, match="只允许合成患者"):
        build_product_context(store, ProductRole.NURSE, patient_id="P-REAL")


def test_operations_snapshot_reads_the_real_offline_story(tmp_path):
    db_path = tmp_path / "product.db"
    start_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    progress = read_competition_demo(db_path)

    snapshot = build_operations_snapshot(store, progress)

    assert progress.stage == CompetitionDemoStage.CANDIDATE_READY
    assert snapshot.stage_label == "等待患者确认"
    assert snapshot.patient_count == 1
    assert snapshot.active_story_count == 1
    assert snapshot.model_source == "确定性离线引擎"
    assert snapshot.alert_count == 0
    assert snapshot.approved_clinical_rule_count == 0
    assert snapshot.integrity_ok is True
    assert snapshot.next_page == "pages/1_patient_followup.py"


def test_operations_evidence_payload_is_truthful_and_secret_free(tmp_path):
    db_path = tmp_path / "product.db"
    start_competition_demo(db_path)
    snapshot = build_operations_snapshot(
        SQLiteStore(db_path, initialize=False),
        read_competition_demo(db_path),
    )

    payload = snapshot.evidence_payload()
    serialized = json.dumps(payload, ensure_ascii=False).lower()

    assert payload["classification"] == "synthetic_only"
    assert payload["role_access"] == "simulated_not_authenticated"
    assert payload["external_send"] == "disabled"
    assert payload["clinical_risk_assessment"] == "not_assessed"
    assert payload["counts"]["alerts"] == 0
    assert "api_key" not in serialized
    assert "mimo_api_key" not in serialized


@pytest.mark.parametrize("task_status", ["requested", "received", "accepted", "in-progress"])
def test_operations_counts_only_open_manual_review_tasks_as_pending(
    task_status, tmp_path
):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)

    snapshot = build_operations_snapshot(
        SQLiteStore(db_path, initialize=False),
        CompetitionDemoProgress(
            stage=CompetitionDemoStage.NURSE_IN_PROGRESS,
            task_status=task_status,
        ),
    )

    assert snapshot.pending_manual_review_count == 1


@pytest.mark.parametrize(
    "stage",
    [
        CompetitionDemoStage.COMMUNICATION_PENDING,
        CompetitionDemoStage.DOCTOR_BRIEF_PENDING,
    ],
)
def test_completed_manual_review_is_separate_from_draft_approval(stage, tmp_path):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)

    snapshot = build_operations_snapshot(
        SQLiteStore(db_path, initialize=False),
        CompetitionDemoProgress(
            stage=stage,
            task_status="completed",
            communication_readiness="pending-approval",
        ),
    )

    assert snapshot.pending_manual_review_count == 0
    assert snapshot.pending_draft_approval_count == 1


def test_operations_fail_closed_for_non_synthetic_registry_row(tmp_path):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)
    store = SQLiteStore(db_path, initialize=False)
    from continucare.db import connect

    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO patients (
                patient_id, display_name, synthetic, pathway_code,
                enrollment_date, next_visit_date, status, created_at
            ) VALUES ('P-REAL', '不应聚合的患者', 0, 'GLP1-14D',
                      '2026-08-01', '2026-08-15', 'active',
                      '2026-08-01T00:00:00+00:00')
            """
        )

    snapshot = build_operations_snapshot(store, CompetitionDemoProgress())

    assert snapshot.integrity_ok is False
    assert snapshot.patients == ()
    assert snapshot.patient_count == 0
    with pytest.raises(ValueError, match="untrusted operations snapshot"):
        snapshot.evidence_payload()


def test_operations_page_fails_closed_when_demo_patient_is_not_synthetic(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)
    from continucare.db import connect

    with connect(db_path) as connection:
        connection.execute(
            "UPDATE patients SET synthetic = 0 WHERE patient_id = 'P-DEMO-001'"
        )
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))

    app = AppTest.from_file(str(OPERATIONS_PAGE), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "运营与治理总台"
    assert any("停止可信聚合" in item.value for item in app.error)
    assert not app.metric
    assert not app.download_button


@pytest.mark.parametrize(
    "updates",
    [
        {"alert_count": 1},
        {"approved_clinical_rule_count": 1},
    ],
)
def test_operations_fail_closed_when_frozen_medical_boundary_is_breached(
    updates, tmp_path
):
    db_path = tmp_path / "product.db"
    initialize_database(db_path)

    snapshot = build_operations_snapshot(
        SQLiteStore(db_path, initialize=False),
        CompetitionDemoProgress(**updates),
    )

    assert snapshot.integrity_ok is False
    with pytest.raises(ValueError, match="untrusted operations snapshot"):
        snapshot.evidence_payload()


def test_role_surfaces_share_the_product_chrome_and_ops_is_read_only():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    patient_source = (ROOT / "pages" / "1_patient_followup.py").read_text(
        encoding="utf-8"
    )
    nurse_source = (ROOT / "pages" / "2_nurse_risk_center.py").read_text(
        encoding="utf-8"
    )
    doctor_source = (ROOT / "pages" / "3_doctor_summary.py").read_text(
        encoding="utf-8"
    )
    operations_source = (ROOT / "pages" / "6_operations.py").read_text(
        encoding="utf-8"
    )

    assert "render_demo_role_hub" in app_source
    assert "ProductRole.PATIENT" in patient_source
    assert "ProductRole.NURSE" in nurse_source
    assert "ProductRole.DOCTOR" in doctor_source
    assert 'st.title("运营与治理总台")' in operations_source
    assert "render_integration_status" in operations_source
    assert "Communication ready-to-send" in operations_source
    assert "save_" not in operations_source
    assert "update_" not in operations_source
