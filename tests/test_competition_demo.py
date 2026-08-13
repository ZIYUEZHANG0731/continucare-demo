from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.db import connect, reset_demo
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.layer4 import Layer4SQLiteStore, ManualReviewBriefService
from continucare.layer4.manual_reviews import SEND_ENABLED
from continucare.pathways import load_builtin_pathways
from continucare.services import competition_demo
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoStage,
    demo_write_guard,
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.manual_review_workflow import ManualReviewWorkflowService


def _after(resource, seconds: int = 1) -> str:
    return (
        datetime.fromisoformat(resource["meta"]["lastUpdated"])
        + timedelta(seconds=seconds)
    ).isoformat()


def _services(db_path):
    progress = read_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    confirmed = ConfirmedReviewService(store, care_agent=agent, care_engine=engine)
    repository = Layer4SQLiteStore(db_path, initialize=False)
    workflow = ManualReviewWorkflowService(store, layer4_store=repository)
    return progress, store, confirmed, repository, workflow


def _confirm(db_path):
    progress, store, confirmed, repository, workflow = _services(db_path)
    record = store.get_agent_run(progress.run_id)
    candidate_ids = [
        item["candidate_id"] for item in record.output_json["candidates"]
    ]
    with demo_write_guard(db_path, expected_generation=progress.generation):
        result = confirmed.accept_all(progress.run_id, candidate_ids)
    return store, repository, workflow, result


def _complete_nurse(db_path):
    store, repository, workflow, confirmed = _confirm(db_path)
    received = workflow.acknowledge(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        note="已收到合成任务。",
        occurred_at=_after(confirmed.task),
    )
    started = workflow.start(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        note="接受并开始核对合成证据。",
        occurred_at=_after(received.task),
    )
    outcome = workflow.record_outcome(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        outcome="evidence_consistent",
        note="已核对原话、确认结果与最终证据链。",
        occurred_at=_after(started.task),
    )
    return store, repository, workflow, outcome


def _briefs(store, repository):
    pathway = load_builtin_pathways().get("GLP1-14D", "1.0.0")
    return ManualReviewBriefService(
        store,
        repository,
        pathway_code=pathway.code,
        pathway_version=pathway.version,
    )


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_story_read_is_read_only_and_does_not_create_database(tmp_path):
    db_path = tmp_path / "missing.db"

    progress = read_competition_demo(db_path)

    assert progress.stage == CompetitionDemoStage.NOT_STARTED
    assert not db_path.exists()


def test_start_atomically_prepares_only_one_unreleased_local_candidate(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "competition.db"
    monkeypatch.setenv("CONTINUCARE_LLM_PROVIDER", "xiaomi_mimo")
    monkeypatch.setenv("CONTINUCARE_LLM_API_KEY", "must-not-be-used")

    progress = start_competition_demo(db_path)
    before = _file_hash(db_path)
    reread = read_competition_demo(db_path)

    assert reread == progress
    assert _file_hash(db_path) == before
    assert progress.stage == CompetitionDemoStage.CANDIDATE_READY
    assert progress.candidate_count == 1
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0
    assert progress.communication_count == 0
    assert progress.manual_brief_count == 0
    assert progress.alert_count == 0
    assert progress.approved_clinical_rule_count == 0
    assert SEND_ENABLED is False
    assert load_builtin_pathways().get("GLP1-14D").clinical_rules == []
    assert not any((tmp_path / f"competition.db{suffix}").exists() for suffix in ("-journal", "-wal", "-shm"))
    with connect(db_path) as connection:
        run = connection.execute("SELECT mode, model_provider FROM agent_runs").fetchone()
    assert tuple(run) == ("local_semantic_mock", None)


def test_failed_start_keeps_the_previous_story_byte_for_byte(monkeypatch, tmp_path):
    db_path = tmp_path / "preserved.db"
    previous = start_competition_demo(db_path)
    before = _file_hash(db_path)

    def fail_after_partial_staging(staging):
        reset_demo(staging)
        raise RuntimeError("sensitive staging failure")

    monkeypatch.setattr(
        competition_demo, "load_manual_review_scenario", fail_after_partial_staging
    )
    with pytest.raises(
        competition_demo.CompetitionDemoStartError,
        match="旧故事未被替换",
    ):
        start_competition_demo(db_path)

    assert _file_hash(db_path) == before
    assert read_competition_demo(db_path).generation == previous.generation
    assert not any(item.name.startswith(".preserved.db.m5d-") for item in tmp_path.iterdir())


def test_concurrent_starts_leave_one_complete_candidate_generation(tmp_path):
    db_path = tmp_path / "concurrent.db"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: start_competition_demo(db_path), range(2)))

    progress = read_competition_demo(db_path)
    assert progress.stage == CompetitionDemoStage.CANDIDATE_READY
    with connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM care_sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fhir_observations").fetchone()[0] == 0


def test_recommended_click_flow_is_derived_from_persisted_facts(tmp_path):
    db_path = tmp_path / "full-flow.db"
    start = start_competition_demo(db_path)
    assert start.stage == CompetitionDemoStage.CANDIDATE_READY

    store, repository, workflow, confirmed = _confirm(db_path)
    requested = read_competition_demo(db_path)
    assert requested.stage == CompetitionDemoStage.TASK_REQUESTED
    assert requested.milestones["patient_confirmed"]
    assert requested.milestones["task_requested"]
    assert requested.questionnaire_response_count == 1
    assert requested.observation_count == 1

    received = workflow.acknowledge(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        note="已收到合成任务。",
        occurred_at=_after(confirmed.task),
    )
    assert read_competition_demo(db_path).stage == CompetitionDemoStage.NURSE_RECEIVED
    started = workflow.start(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        note="接受并开始核对合成证据。",
        occurred_at=_after(received.task),
    )
    assert read_competition_demo(db_path).stage == CompetitionDemoStage.NURSE_IN_PROGRESS
    outcome = workflow.record_outcome(
        patient_id=DEMO_PATIENT_ID,
        task_id=confirmed.task["id"],
        outcome="evidence_consistent",
        note="已核对原话、确认结果与最终证据链。",
        occurred_at=_after(started.task),
    )
    pending = read_competition_demo(db_path)
    assert pending.stage == CompetitionDemoStage.COMMUNICATION_PENDING
    assert pending.communication_readiness == "pending-approval"

    briefs = _briefs(store, repository)
    pending_summary = briefs.generate(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        generated_at=_after(outcome.task),
    )
    pending_brief = read_competition_demo(db_path)
    assert pending_brief.stage == CompetitionDemoStage.DOCTOR_BRIEF_PENDING
    assert pending_brief.summary_version == pending_summary.version

    approved = workflow.approve_draft(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        communication_id=outcome.communication["id"],
        note="明确批准 ready-to-send；仍未发送。",
        occurred_at=(
            datetime.fromisoformat(pending_summary.created_at) + timedelta(seconds=1)
        ).isoformat(),
    )
    ready = read_competition_demo(db_path)
    assert ready.stage == CompetitionDemoStage.COMMUNICATION_READY
    assert ready.milestones["doctor_brief_pending"]
    assert not ready.milestones["doctor_brief_ready"]

    final_summary = briefs.generate(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        generated_at=_after(approved.communication),
    )
    complete = read_competition_demo(db_path)
    assert complete.stage == CompetitionDemoStage.STORY_COMPLETE
    assert complete.summary_version == final_summary.version
    assert complete.milestones["doctor_brief_ready"]
    assert complete.milestones["story_complete"]
    assert complete.alert_count == 0
    assert complete.approved_clinical_rule_count == 0
    assert complete.communication_readiness == "ready-to-send"


def test_legal_approval_before_first_brief_does_not_invent_pending_brief(tmp_path):
    db_path = tmp_path / "alternate-order.db"
    start_competition_demo(db_path)
    store, repository, workflow, outcome = _complete_nurse(db_path)
    approved = workflow.approve_draft(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        communication_id=outcome.communication["id"],
        note="先明确批准，尚未生成医生简报。",
        occurred_at=_after(outcome.task),
    )

    progress = read_competition_demo(db_path)
    assert progress.stage == CompetitionDemoStage.COMMUNICATION_READY
    assert not progress.milestones["doctor_brief_pending"]

    _briefs(store, repository).generate(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        generated_at=_after(approved.communication),
    )
    assert read_competition_demo(db_path).stage == CompetitionDemoStage.STORY_COMPLETE


def test_knowledge_availability_is_not_a_clinical_completion_gate(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "knowledge-independent.db"
    start_competition_demo(db_path)
    store, repository, workflow, outcome = _complete_nurse(db_path)
    approved = workflow.approve_draft(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        communication_id=outcome.communication["id"],
        note="批准合成草稿。",
        occurred_at=_after(outcome.task),
    )
    _briefs(store, repository).generate(
        patient_id=DEMO_PATIENT_ID,
        task_id=outcome.task["id"],
        generated_at=_after(approved.communication),
    )
    monkeypatch.setattr(
        competition_demo,
        "_knowledge_status",
        lambda: (False, "independent registry unavailable"),
    )

    progress = read_competition_demo(db_path)
    assert progress.stage == CompetitionDemoStage.STORY_COMPLETE
    assert not progress.knowledge_available


def test_explicit_restart_removes_the_old_chain_and_stale_writes_are_rejected(
    tmp_path,
):
    db_path = tmp_path / "restart.db"
    original = start_competition_demo(db_path)
    _confirm(db_path)
    restarted = start_competition_demo(db_path)

    assert restarted.generation != original.generation
    assert restarted.stage == CompetitionDemoStage.CANDIDATE_READY
    assert restarted.questionnaire_response_count == 0
    assert restarted.observation_count == 0
    assert restarted.manual_task_count == 0
    with pytest.raises(CompetitionDemoConflict, match="另一标签页重新开始"):
        with demo_write_guard(db_path, expected_generation=original.generation):
            pass


def test_patient_page_no_longer_creates_a_session_on_load_and_knowledge_stays_isolated():
    root = __import__("pathlib").Path(__file__).parents[1]
    patient_source = (root / "pages" / "1_patient_followup.py").read_text("utf-8")
    knowledge_source = (root / "pages" / "5_knowledge_evidence.py").read_text("utf-8")

    assert "start_or_resume(" not in patient_source
    assert "read_competition_demo" in patient_source
    assert "with demo_write_guard(settings.db_path):" not in patient_source
    assert "expected_generation=progress.generation" in patient_source
    assert "continucare.services.competition_demo" not in knowledge_source
    assert "continucare.db" not in knowledge_source
    assert "patient_id" not in knowledge_source
