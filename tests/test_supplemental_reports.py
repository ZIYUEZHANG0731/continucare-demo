from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import (
    SemanticModelConfig,
    UnconfiguredModelAdapter,
)
from continucare.care_engine import CareEngine
from continucare.db import initialize_database
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.services.competition_demo import (
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.supplemental_reports import (
    read_supplemental_reports,
    resolve_supplemental_turn,
    review_supplemental_report,
    submit_supplemental_report_turn,
)
from continucare.terminology import (
    load_cn_glp1_terminology_catalog,
    load_glp1_symptom_catalog,
)


ROOT = Path(__file__).parents[1]
PATIENT_PAGE = ROOT / "pages" / "1_patient_followup.py"
NURSE_PAGE = ROOT / "pages" / "2_nurse_risk_center.py"
DOCTOR_PAGE = ROOT / "pages" / "3_doctor_summary.py"


def test_existing_supplemental_table_migrates_before_new_index(tmp_path):
    db_path = tmp_path / "old-supplemental-schema.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE patient_supplemental_reports (
                report_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                source_run_id TEXT NOT NULL UNIQUE,
                original_text TEXT NOT NULL,
                structured_items_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT
            )
            """
        )

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(patient_supplemental_reports)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(patient_supplemental_reports)"
            )
        }
    assert {
        "anchor_session_id",
        "questionnaire_response_id",
        "observation_ids_json",
        "provenance_id",
        "report_kind",
        "handoff_reason_code",
        "handoff_policy_version",
    } <= columns
    assert "idx_patient_supplemental_reports_queue" in indexes


def _adapter(monkeypatch, content, *, provider="xiaomi_mimo"):
    doubao = provider == "volcengine_doubao"
    key_env = (
        "DOUBAO_SUPPLEMENTAL_TEST_KEY"
        if doubao
        else "MIMO_SUPPLEMENTAL_TEST_KEY"
    )
    monkeypatch.setenv(key_env, "not-a-real-secret")
    config = SemanticModelConfig(
        provider=provider,
        model_name="doubao-seed-2-0-lite-260215" if doubao else "mimo-v2.5",
        base_url=(
            "https://ark.cn-beijing.volces.com/api/v3"
            if doubao
            else "https://api.xiaomimimo.com/v1"
        ),
        api_key_env=key_env,
        prompt_version=(
            "doubao-semantic-extraction-v1"
            if doubao
            else "mimo-semantic-extraction-v1"
        ),
        timeout_seconds=2,
    )

    def transport(_url, _headers, _payload, _timeout):
        return {
            "id": "supplemental-test-request",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(content, ensure_ascii=False),
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    return MiMoSemanticAdapter(config, transport=transport)


def _completed_daily_checkin(db_path):
    progress = start_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
    )
    record = store.get_agent_run(progress.run_id)
    assert record is not None
    result = json.loads(
        record.output_json
        if isinstance(record.output_json, str)
        else json.dumps(record.output_json)
    )
    ConfirmedReviewService(store, care_agent=agent, care_engine=engine).accept_all(
        record.run_id,
        [item["candidate_id"] for item in result["candidates"]],
    )
    return store, record.session_id, record.run_id


def _submit(
    monkeypatch,
    db_path,
    *,
    session_id,
    message_text,
    content,
    provider="xiaomi_mimo",
):
    story = read_competition_demo(db_path)
    supplemental = read_supplemental_reports(db_path, session_id=session_id)
    return submit_supplemental_report_turn(
        db_path,
        session_id=session_id,
        expected_story_generation=story.generation,
        expected_supplemental_generation=supplemental.generation,
        message_text=message_text,
        synthetic_confirmed=True,
        model_adapter=_adapter(monkeypatch, content, provider=provider),
    )


def _accept(db_path, *, session_id, projection, clarification_options=None):
    story = read_competition_demo(db_path)
    assert projection.pending_run_id is not None
    return resolve_supplemental_turn(
        db_path,
        session_id=session_id,
        run_id=projection.pending_run_id,
        decision="accepted",
        expected_story_generation=story.generation,
        expected_supplemental_generation=projection.generation,
        clarification_options=clarification_options,
    )


def test_unmatched_supplement_creates_independent_qr_without_fake_observation(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "supplemental-raw.db"
    store, session_id, daily_run_id = _completed_daily_checkin(db_path)
    before_anchor = store.get_care_session(session_id)
    before_observation_ids = {
        item.observation_id for item in store.list_observations(DEMO_PATIENT_ID)
    }

    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我刚刚觉得左手有点僵，想补充告诉护士。",
        content={"blocked": False, "items": []},
    )

    assert submitted.pending_items == ()
    assert submitted.pending_clarifications == ()
    accepted = _accept(db_path, session_id=session_id, projection=submitted)
    report = accepted.reports[0]

    assert report.structured_items == ()
    assert report.questionnaire_response_id
    assert report.observation_ids == ()
    assert report.provenance_id
    assert (
        store.get_questionnaire_response(report.questionnaire_response_id) is not None
    )
    assert store.get_care_session(session_id) == before_anchor
    assert read_competition_demo(db_path).run_id == daily_run_id
    assert {
        item.observation_id for item in store.list_observations(DEMO_PATIENT_ID)
    } == before_observation_ids

    reviewed = review_supplemental_report(
        db_path,
        session_id=session_id,
        report_id=report.report_id,
        expected_story_generation=read_competition_demo(db_path).generation or "",
        expected_supplemental_generation=accepted.generation,
        note="已查看患者确认的补充上报；未作临床风险判断。",
    )
    assert reviewed.reports[0].status == "reviewed"
    assert store.get_care_session(session_id) == before_anchor


def test_doubao_supplemental_turn_can_be_resolved(monkeypatch, tmp_path):
    db_path = tmp_path / "supplemental-doubao.db"
    _, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我刚刚觉得左手有点僵，想补充告诉护士。",
        content={"blocked": False, "items": []},
        provider="volcengine_doubao",
    )

    accepted = _accept(db_path, session_id=session_id, projection=submitted)

    assert accepted.pending_run_id is None
    assert len(accepted.reports) == 1
    assert accepted.reports[0].original_text == (
        "我刚刚觉得左手有点僵，想补充告诉护士。"
    )


def test_fixed_cn_supplement_maps_to_confirmed_observation(monkeypatch, tmp_path):
    db_path = tmp_path / "supplemental-fixed.db"
    store, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我现在又恶心了。",
        content={
            "blocked": False,
            "items": [
                {
                    "link_id": "nausea-present",
                    "answer": True,
                    "evidence_text": "又恶心了",
                    "subject": "patient",
                    "temporality": "current",
                    "negated": False,
                }
            ],
        },
    )
    assert [item["link_id"] for item in submitted.pending_items] == [
        "nausea-present"
    ]

    accepted = _accept(db_path, session_id=session_id, projection=submitted)
    report = accepted.reports[0]
    observations = store.list_observations_for_message(
        report.questionnaire_response_id or ""
    )

    assert len(observations) == 1
    assert observations[0].observation_id in report.observation_ids
    assert observations[0].evidence.knowledge_release_id
    match = observations[0].evidence.terminology_match or {}
    assert match["source_catalog_id"] == load_cn_glp1_terminology_catalog().catalog_id


def test_fixed_vomiting_count_suppresses_duplicate_dynamic_symptom(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "supplemental-vomiting-count.db"
    store, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="过去24小时我呕吐了3次。",
        content={
            "blocked": False,
            "items": [
                {
                    "link_id": "vomiting-count-24h",
                    "answer": 3,
                    "evidence_text": "过去24小时我呕吐了3次",
                    "subject": "patient",
                    "temporality": "explicit_24h",
                    "negated": False,
                }
            ],
        },
    )

    assert [item["link_id"] for item in submitted.pending_items] == [
        "vomiting-count-24h"
    ]
    accepted = _accept(db_path, session_id=session_id, projection=submitted)
    report = accepted.reports[0]
    observations = store.list_observations_for_message(
        report.questionnaire_response_id or ""
    )
    assert len(observations) == 1
    assert observations[0].code == "94070-0"
    assert observations[0].value == 3


def test_dynamic_catalog_supplement_restores_original_symptom_observation_path(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "supplemental-dynamic.db"
    store, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我现在还在拉肚子。",
        content={"blocked": False, "items": []},
    )

    assert [item["link_id"] for item in submitted.pending_items] == [
        "patient-reported-symptom::diarrhea"
    ]
    assert submitted.pending_items[0]["source_mode"] == "deterministic_catalog"
    assert submitted.pending_clarifications == ()
    accepted = _accept(db_path, session_id=session_id, projection=submitted)
    report = accepted.reports[0]
    observations = store.list_observations_for_message(
        report.questionnaire_response_id or ""
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.resource["code"]["coding"][0]["code"] == "62315008"
    assert observation.evidence.knowledge_release_id is None
    assert observation.evidence.observation_mapping_sha256 is None
    match = observation.evidence.terminology_match or {}
    assert match["source_catalog_id"] == load_glp1_symptom_catalog().catalog_id
    assert match["source_catalog_status"] == "draft-prototype-verified"
    assert match["approval_status"] == "prototype-verified"
    assert match["target_hospital_validation_required"] is True


def test_ambiguous_dynamic_symptom_requires_exact_patient_choice(monkeypatch, tmp_path):
    db_path = tmp_path / "supplemental-ambiguous.db"
    store, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="过去24小时我有点头晕。",
        content={"blocked": False, "items": []},
    )

    assert submitted.pending_items == ()
    assert len(submitted.pending_clarifications) == 1
    clarification = submitted.pending_clarifications[0]
    option_ids = {item["option_id"] for item in clarification["options"]}
    assert "concept::dizziness" in option_ids

    with pytest.raises(ValueError, match="逐项回答全部澄清问题"):
        _accept(db_path, session_id=session_id, projection=submitted)
    assert read_supplemental_reports(
        db_path, session_id=session_id
    ).pending_run_id == submitted.pending_run_id

    selections = {clarification["clarification_id"]: "concept::dizziness"}
    accepted = _accept(
        db_path,
        session_id=session_id,
        projection=submitted,
        clarification_options=selections,
    )
    report = accepted.reports[0]
    assert report.structured_items[0]["temporality"] == "explicit_24h"
    assert report.structured_items[0]["effective_time"]["kind"] == "rolling_24h"
    observations = store.list_observations_for_message(
        report.questionnaire_response_id or ""
    )
    assert len(observations) == 1
    assert observations[0].resource["code"]["coding"][0]["code"] == "404640003"

    replay = resolve_supplemental_turn(
        db_path,
        session_id=session_id,
        run_id=submitted.pending_run_id or "",
        decision="accepted",
        expected_story_generation=read_competition_demo(db_path).generation or "",
        expected_supplemental_generation=accepted.generation,
        clarification_options=selections,
    )
    assert replay == accepted


def test_unspecified_ambiguous_symptom_option_explicitly_confirms_current_time(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "supplemental-ambiguous-unspecified.db"
    _, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我有点头晕。",
        content={"blocked": False, "items": []},
    )
    clarification = submitted.pending_clarifications[0]
    assert "同时确认这是您现在仍有" in clarification["prompt"]
    concept_options = [
        item
        for item in clarification["options"]
        if item["option_id"].startswith("concept::")
    ]
    assert concept_options
    assert all("现在仍有" in item["label"] for item in concept_options)


@pytest.mark.parametrize(
    ("column", "tampered_value"),
    [
        ("model_provider", "not-mimo"),
        ("terminology_catalog_sha256", "0" * 64),
    ],
)
def test_finalization_rechecks_live_model_and_terminology_boundary(
    monkeypatch, tmp_path, column, tampered_value
):
    db_path = tmp_path / f"supplemental-tamper-{column}.db"
    store, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我刚刚觉得左手有点僵，想补充告诉护士。",
        content={"blocked": False, "items": []},
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE agent_runs SET {column}=? WHERE run_id=?",
            (tampered_value, submitted.pending_run_id),
        )

    with pytest.raises(ValueError, match="来源不可信"):
        _accept(db_path, session_id=session_id, projection=submitted)

    projection = read_supplemental_reports(db_path, session_id=session_id)
    assert projection.pending_run_id == submitted.pending_run_id
    assert projection.reports == ()
    child = store.get_care_session(
        store.get_agent_run(submitted.pending_run_id or "").session_id
    )
    assert child is not None
    assert child.questionnaire_response_id is None


def test_supplemental_fhir_record_is_visible_on_all_three_role_pages(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "supplemental-pages.db"
    _, session_id, _ = _completed_daily_checkin(db_path)
    submitted = _submit(
        monkeypatch,
        db_path,
        session_id=session_id,
        message_text="我现在还在拉肚子。",
        content={"blocked": False, "items": []},
    )
    accepted = _accept(db_path, session_id=session_id, projection=submitted)
    assert accepted.reports[0].observation_ids

    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)
    for page in (PATIENT_PAGE, DOCTOR_PAGE):
        app = AppTest.from_file(str(page), default_timeout=10).run()
        assert not app.exception
        rendered = "\n".join(
            str(item.value)
            for collection in (
                app.markdown,
                app.caption,
            )
            for item in collection
        )
        assert "拉肚子" in rendered
        assert "QuestionnaireResponse/" in rendered
        assert "Observation/" in rendered or "患者报告 腹泻" in rendered

    nurse = AppTest.from_file(str(NURSE_PAGE), default_timeout=10).run()
    assert not nurse.exception
    nurse_rendered = "\n".join(
        str(item.value)
        for collection in (nurse.markdown, nurse.caption)
        for item in collection
    )
    assert "拉肚子" in nurse_rendered
    assert "QuestionnaireResponse/" not in nurse_rendered
    assert "Observation/" not in nurse_rendered
