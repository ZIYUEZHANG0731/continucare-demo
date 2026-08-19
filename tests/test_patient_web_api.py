from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.errors import ModelRequestError
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import SemanticModelConfig
from continucare.db import reset_demo
from continucare.doctor_planning import (
    build_followup_plan_proposal,
    confirm_followup_plan,
)
from continucare.patient_mobile import (
    PatientMobileBoundaryError,
    _candidate_label,
    build_patient_mobile_state,
    explicit_unknown_command,
    finalize_command,
    remove_additional_report_command,
    resolve_candidates_command,
    resolve_clarification_command,
    submit_chat_command,
)
from continucare.patient_web import CSRF_TOKEN, _command, api_state, app, spa
from continucare.nurse_portal import build_nurse_portal_state
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoStartError,
    activate_competition_plan,
    read_competition_demo,
    start_next_competition_checkin,
    submit_activated_plan_feedback,
    submit_patient_chat_turn,
)
from continucare.services.supplemental_reports import read_supplemental_reports


def _configured_db(tmp_path, monkeypatch):
    db_path = tmp_path / "patient-web.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    reset_demo(db_path)
    return db_path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stub_mimo_adapter(
    monkeypatch, *, items=None, symptom_mentions=None, fail=False, captured=None
):
    monkeypatch.setenv("MIMO_PATIENT_WEB_TEST_KEY", "synthetic-test-key")
    config = SemanticModelConfig(
        provider="xiaomi_mimo",
        model_name="mimo-v2.5",
        base_url="https://api.xiaomimimo.com/v1",
        api_key_env="MIMO_PATIENT_WEB_TEST_KEY",
        safety_llm_enabled=False,
        language_llm_enabled=False,
        summary_llm_enabled=False,
        timeout_seconds=2,
    )

    def transport(_url, _headers, _payload, _timeout):
        if captured is not None:
            captured.update({"payload": _payload, "timeout": _timeout})
        if fail:
            raise ModelRequestError("synthetic provider failure")
        return {
            "id": "patient-web-stub",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "blocked": False,
                                "items": [
                                    {
                                        "link_id": "nausea-present",
                                        "answer": True,
                                        "evidence_text": "我今天有恶心",
                                        "subject": "patient",
                                        "temporality": "current",
                                        "negated": False,
                                    }
                                ] if items is None else items,
                                "symptom_mentions": symptom_mentions or [],
                            },
                            ensure_ascii=False,
                        ),
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


def _stage_complete_one_turn_draft(
    tmp_path, monkeypatch, *, symptom_mentions=None, db_path=None, activate=True
):
    if db_path is None:
        db_path = _configured_db(tmp_path, monkeypatch)
    if activate:
        activate_competition_plan(db_path, expected_generation=None)
    monkeypatch.setattr(
        "continucare.patient_mobile.competition_mimo_configured", lambda: True
    )
    weight_text = "今天体重65.5公斤，" if not activate else ""
    message = (
        f"{weight_text}现在有恶心，程度中度，过去24小时吐了2次、喝了800毫升水，"
        "现在没有腹痛"
    )
    if symptom_mentions:
        message += "，还在拉肚子"
    items = []
    if not activate:
        items.append({
            "link_id": "body-weight",
            "answer": {
                "value": 65.5,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "evidence_text": "今天体重65.5公斤",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        })
    items.extend([
        {
            "link_id": "nausea-present",
            "answer": True,
            "evidence_text": "现在有恶心",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        },
        {
            "link_id": "nausea-severity",
            "answer": "LA6751-7",
            "evidence_text": "程度中度",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        },
        {
            "link_id": "vomiting-count-24h",
            "answer": 2,
            "evidence_text": "过去24小时吐了2次",
            "subject": "patient",
            "temporality": "explicit_24h",
            "negated": False,
        },
        {
            "link_id": "fluid-intake-24h-estimated",
            "answer": {
                "value": 800,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "evidence_text": "喝了800毫升水",
            "subject": "patient",
            "temporality": "explicit_24h",
            "negated": False,
        },
        {
            "link_id": "abdominal-pain-present",
            "answer": False,
            "evidence_text": "现在没有腹痛",
            "subject": "patient",
            "temporality": "current",
            "negated": True,
        },
    ])
    adapter = _stub_mimo_adapter(
        monkeypatch, items=items, symptom_mentions=symptom_mentions
    )
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda db_path, **kwargs: submit_patient_chat_turn(
            db_path, model_adapter=adapter, **kwargs
        ),
    )
    state = build_patient_mobile_state()
    submit_chat_command(
        {
            "generation": state["generation"],
            "message": message,
            "syntheticConfirmed": True,
        }
    )
    state = build_patient_mobile_state()
    assert state["kind"] == "final_review"
    return db_path, state


def _asgi_request(method, path, *, headers=None, body=b""):
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8510),
    }
    delivered = False
    messages = []

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(item for item in messages if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    return start["status"], response_headers, response_body


def _request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    path_params: dict[str, str] | None = None,
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8510),
        "path_params": path_params or {},
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_mobile_state_is_server_discriminated_and_recovers_after_activation(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)

    waiting = build_patient_mobile_state()
    assert waiting["kind"] == "waiting_doctor"
    assert waiting["allowedActions"] == []

    activate_competition_plan(db_path, expected_generation=None)
    collecting = build_patient_mobile_state()
    assert collecting["kind"] == "collecting"
    assert collecting["allowedActions"] == ["chat"]
    assert collecting["patient"]["synthetic"] is True
    assert "endpoint" not in str(collecting).lower()
    assert "api_key" not in str(collecting).lower()


def test_mobile_state_fails_closed_for_non_synthetic_patient(tmp_path, monkeypatch):
    db_path = _configured_db(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE patients SET synthetic=0")

    state = build_patient_mobile_state()

    assert state["kind"] == "fail_closed"
    assert "allowedActions" not in state


def test_dynamic_terminology_candidate_uses_patient_label_not_internal_link():
    candidate = SimpleNamespace(
        candidate_id="candidate-diarrhea",
        link_id="patient-reported-symptom::diarrhea",
        answer=True,
        evidence_text="我现在还在拉肚子",
        terminology_match=SimpleNamespace(
            preferred_zh="腹泻",
            source_catalog_status="draft-prototype-verified",
        ),
    )

    projected = _candidate_label(candidate, {"item": []})

    assert projected["question"] == "症状：腹泻"
    assert projected["proposed"] == "确认上报"
    assert projected["linkId"] == "patient-reported-symptom::diarrhea"


def test_chat_revision_target_is_revalidated_before_any_mimo_call(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    progress = activate_competition_plan(db_path, expected_generation=None)
    calls = []
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    before = _sha256(db_path)

    with pytest.raises(PatientMobileBoundaryError):
        submit_chat_command(
            {
                "generation": progress.generation,
                "message": "改成2次",
                "selectedRevisionLinkId": "arbitrary-link",
                "syntheticConfirmed": True,
            }
        )

    assert calls == []
    assert _sha256(db_path) == before


@pytest.mark.parametrize("message", ["有", "有恶心"])
def test_first_direct_reply_is_scoped_to_the_visible_pathway_question(
    tmp_path, monkeypatch, message
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    captured = {}
    adapter = _stub_mimo_adapter(
        monkeypatch,
        items=[
            {
                "link_id": "nausea-present",
                "answer": True,
                "evidence_text": message,
                "subject": "patient",
                "temporality": "unspecified",
                "negated": False,
            }
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda db_path, **kwargs: submit_patient_chat_turn(
            db_path, model_adapter=adapter, **kwargs
        ),
    )

    submit_chat_command(
        {
            "generation": activated.generation,
            "message": message,
            "syntheticConfirmed": True,
        }
    )

    state = build_patient_mobile_state()
    assert state["kind"] == "collecting"
    assert state["nextLinkId"] == "nausea-severity"
    assert all(
        item.get("kind") != "confirmed_record" for item in state["history"]
    )
    assert any(item.get("kind") == "draft_record" for item in state["history"])
    system_prompt = captured["payload"]["messages"][0]["content"]
    assert '"link_id":"nausea-present"' in system_prompt
    assert '"link_id":"vomiting-count-24h"' not in system_prompt


def test_same_generation_parallel_candidate_confirmation_applies_once(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    ready = submit_activated_plan_feedback(
        db_path,
        expected_generation=activated.generation,
        use_mimo=False,
    )
    payload = {"generation": ready.generation, "decision": "accepted"}

    def invoke():
        try:
            resolve_candidates_command(payload)
            return "applied"
        except CompetitionDemoConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: invoke(), range(2)))

    progress = read_competition_demo(db_path)
    assert sorted(outcomes) == ["applied", "conflict"]
    assert list(progress.candidate_decisions.values()).count("accepted") == 1


def test_http_chat_success_round_trip_recovers_server_candidate_state(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    adapter = _stub_mimo_adapter(monkeypatch)
    monkeypatch.setattr(
        "continucare.patient_mobile.competition_mimo_configured", lambda: True
    )

    def submit_with_stub(db_path, **kwargs):
        return submit_patient_chat_turn(db_path, model_adapter=adapter, **kwargs)

    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn", submit_with_stub
    )
    status, state_headers, state_body = _asgi_request(
        "GET", "/api/state", headers={"host": "127.0.0.1:8510"}
    )
    assert status == 200
    state = json.loads(state_body)["data"]
    assert state["kind"] == "collecting"
    assert state["generation"] == activated.generation

    chat_payload = json.dumps(
        {
            "generation": state["generation"],
            "message": "我今天有恶心",
            "syntheticConfirmed": True,
        },
        ensure_ascii=False,
    ).encode()
    status, _, body = _asgi_request(
        "POST",
        "/api/chat",
        body=chat_payload,
        headers={
            "host": "127.0.0.1:8510",
            "origin": "http://127.0.0.1:8510",
            "x-continucare-csrf": state_headers["x-continucare-csrf"],
            "content-type": "application/json",
            "content-length": str(len(chat_payload)),
        },
    )
    assert status == 200
    assert json.loads(body) == {"ok": True}

    status, state_headers, state_body = _asgi_request(
        "GET", "/api/state", headers={"host": "127.0.0.1:8510"}
    )
    collecting_state = json.loads(state_body)["data"]
    assert status == 200
    assert collecting_state["kind"] == "collecting"
    assert [item["label"] for item in collecting_state["quickReplies"]] == [
        "轻度",
        "中度",
        "重度",
    ]
    drafts = [
        item
        for item in collecting_state["history"]
        if item.get("kind") == "draft_record"
    ]
    assert drafts[-1]["items"] == [
        {
            "linkId": "nausea-present",
            "label": "恶心",
            "value": "是",
            "evidence": "我今天有恶心",
        }
    ]
    assert list(read_competition_demo(db_path).candidate_decisions.values()) == [
        "drafted"
    ]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_action_resolutions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type='semantic_candidate_patient_decision'"
        ).fetchone()[0] == 0

    focused_request = {}
    adapter = _stub_mimo_adapter(
        monkeypatch,
        # A governed, visibly offered choice must remain writable even when the
        # model omits it. The stored candidate is attributed to the patient's
        # exact selection rather than falsely attributed to model inference.
        items=[],
        captured=focused_request,
    )
    moderate_payload = json.dumps(
        {
            "generation": collecting_state["generation"],
            "message": "中度",
            "syntheticConfirmed": True,
        },
        ensure_ascii=False,
    ).encode()
    status, _, body = _asgi_request(
        "POST",
        "/api/chat",
        body=moderate_payload,
        headers={
            "host": "127.0.0.1:8510",
            "origin": "http://127.0.0.1:8510",
            "x-continucare-csrf": state_headers["x-continucare-csrf"],
            "content-type": "application/json",
            "content-length": str(len(moderate_payload)),
        },
    )
    assert status == 200, body
    status, _, state_body = _asgi_request(
        "GET", "/api/state", headers={"host": "127.0.0.1:8510"}
    )
    moderate_state = json.loads(state_body)["data"]
    assert status == 200
    assert moderate_state["kind"] == "collecting"
    assert moderate_state["nextLinkId"] == "vomiting-count-24h"
    assert any(
        item.get("kind") == "draft_record"
        and item["items"][0]["linkId"] == "nausea-severity"
        and item["items"][0]["value"] == "中度"
        for item in moderate_state["history"]
    )
    latest = next(
        item
        for item in SQLiteStore(db_path, initialize=False).list_agent_runs(
            read_competition_demo(db_path).session_id
        )
        if item.input_text == "中度"
    )
    stored_result = latest.output_json
    assert stored_result["candidates"][0]["source_mode"] == "patient_selection"
    assert stored_result["candidates"][0]["answer"] == "LA6751-7"
    system_prompt = focused_request["payload"]["messages"][0]["content"]
    assert "one governed choice label" in system_prompt
    assert '"link_id":"nausea-severity"' in system_prompt


def test_product_checkin_uses_one_final_confirmation_for_the_whole_draft(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activate_competition_plan(db_path, expected_generation=None)
    monkeypatch.setattr(
        "continucare.patient_mobile.competition_mimo_configured", lambda: True
    )

    def send(message, item, *, symptom_mentions=None):
        adapter = _stub_mimo_adapter(
            monkeypatch, items=[item], symptom_mentions=symptom_mentions
        )
        monkeypatch.setattr(
            "continucare.patient_mobile.submit_patient_chat_turn",
            lambda db_path, **kwargs: submit_patient_chat_turn(
                db_path, model_adapter=adapter, **kwargs
            ),
        )
        state = build_patient_mobile_state()
        submit_chat_command(
            {
                "generation": state["generation"],
                "message": message,
                "syntheticConfirmed": True,
            }
        )
        updated = build_patient_mobile_state()
        assert updated["kind"] != "candidate_review"
        return updated

    state = send(
        "有恶心，还在拉肚子和便秘",
        {
            "link_id": "nausea-present",
            "answer": True,
            "evidence_text": "有恶心",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        },
        symptom_mentions=[
            {
                "symptom_text": "拉肚子",
                "evidence_text": "拉肚子",
                "subject": "patient",
                "temporality": "current",
                "negated": False,
            },
            {
                "symptom_text": "便秘",
                "evidence_text": "便秘",
                "subject": "patient",
                "temporality": "current",
                "negated": False,
            },
        ],
    )
    assert state["nextLinkId"] == "nausea-severity"
    assert state["history"][2]["kind"] == "draft_record"
    assert any(
        item["label"] == "其他症状 · 腹泻"
        for row in state["history"]
        if row.get("kind") == "draft_record"
        for item in row["items"]
    ), state["history"]
    state = send(
        "中度",
        {
            "link_id": "nausea-severity",
            "answer": "LA6751-7",
            "evidence_text": "中度",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        },
    )
    state = send(
        "吐了2次",
        {
            "link_id": "vomiting-count-24h",
            "answer": 2,
            "evidence_text": "吐了2次",
            "subject": "patient",
            "temporality": "explicit_24h",
            "negated": False,
        },
    )
    explicit_unknown_command({"generation": state["generation"]})
    state = send(
        "没有腹痛",
        {
            "link_id": "abdominal-pain-present",
            "answer": False,
            "evidence_text": "没有腹痛",
            "subject": "patient",
            "temporality": "current",
            "negated": True,
        },
    )
    assert state["kind"] == "final_review"
    assert len(state["answers"]) == 5
    assert sorted(item["label"] for item in state["additionalReports"]) == [
        "其他症状 · 便秘",
        "其他症状 · 腹泻",
    ]

    revision_adapter = _stub_mimo_adapter(
        monkeypatch,
        items=[
            {
                "link_id": "abdominal-pain-present",
                "answer": True,
                "evidence_text": "改成有腹痛",
                "subject": "patient",
                "temporality": "current",
                "negated": False,
            }
        ],
    )
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda db_path, **kwargs: submit_patient_chat_turn(
            db_path, model_adapter=revision_adapter, **kwargs
        ),
    )
    submit_chat_command(
        {
            "generation": state["generation"],
            "message": "改成有腹痛",
            "selectedRevisionLinkId": "abdominal-pain-present",
            "syntheticConfirmed": True,
        }
    )
    state = build_patient_mobile_state()
    assert state["kind"] == "final_review"
    assert next(
        item["value"]
        for item in state["answers"]
        if item["linkId"] == "abdominal-pain-present"
    ) == "是"
    constipation = next(
        item
        for item in state["additionalReports"]
        if item["label"] == "其他症状 · 便秘"
    )
    remove_additional_report_command(
        {
            "generation": state["generation"],
            "reportId": constipation["reportId"],
        }
    )
    state = build_patient_mobile_state()
    assert state["kind"] == "final_review"
    assert [item["label"] for item in state["additionalReports"]] == [
        "其他症状 · 腹泻"
    ]

    progress = read_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0
    assert len(store.list_active_provisional_answer_contexts(progress.session_id)) == 5
    assert len(store.list_active_provisional_symptom_reports(progress.session_id)) == 1
    assert store.list_active_answer_contexts(progress.session_id) == []
    assert store.list_active_symptom_reports(progress.session_id) == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_action_resolutions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type='semantic_candidate_patient_decision'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type='patient_draft_symptom_removed'"
        ).fetchone()[0] == 1

    finalize_command({"generation": state["generation"]})

    completed = read_competition_demo(db_path)
    assert completed.questionnaire_response_count == 1
    # The prototype dynamic catalog report is retained for the nurse, but the
    # locked CN release does not promote it into a coded Observation.
    assert completed.observation_count == 4
    assert completed.manual_task_count == 1
    assert store.list_active_provisional_answer_contexts(completed.session_id) == []
    assert store.list_active_provisional_symptom_reports(completed.session_id) == []
    assert len(store.list_active_answer_contexts(completed.session_id)) == 5
    assert len(store.list_active_symptom_reports(completed.session_id)) == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type='patient_checkin_submitted'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_action_resolutions "
            "WHERE decision='accepted'"
        ).fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_action_resolutions "
            "WHERE decision='rejected'"
        ).fetchone()[0] == 2


def test_confirmed_plan_accepts_two_daily_patient_submissions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONTINUCARE_SYNTHETIC_NOW", "2026-08-16T08:00:00+08:00")
    db_path = _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()
    assert proposal["period"]["startDate"] == "2026-08-16"
    assert proposal["period"]["endDate"] >= "2026-08-17"
    saved_plan = confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [
                {
                    "metricId": item["metricId"],
                    "frequency": "daily",
                }
                for item in proposal["candidates"]
            ],
        }
    )
    _, first_state = _stage_complete_one_turn_draft(
        tmp_path, monkeypatch, db_path=db_path, activate=False
    )
    finalize_command({"generation": first_state["generation"]})
    first = read_competition_demo(db_path)
    first_session_id = first.session_id
    assert first.session_status == "completed"

    store = SQLiteStore(db_path, initialize=False)
    first_session = store.get_care_session(first_session_id)
    assert saved_plan["activationSessionId"] == first_session_id

    second = start_next_competition_checkin(
        db_path,
        expected_generation=first.generation,
        now="2026-08-17T08:00:00+08:00",
    )
    assert second.session_id != first_session_id
    assert second.session_status == "in_progress"
    second_session = store.get_care_session(second.session_id)
    assert second_session.created_at == "2026-08-17T00:00:00+00:00"
    assert (
        second_session.pathway_code,
        second_session.pathway_version,
        second_session.questionnaire_canonical,
        second_session.questionnaire_version,
        second_session.knowledge_release_id,
    ) == (
        first_session.pathway_code,
        first_session.pathway_version,
        first_session.questionnaire_canonical,
        first_session.questionnaire_version,
        first_session.knowledge_release_id,
    )

    monkeypatch.setenv("CONTINUCARE_SYNTHETIC_NOW", "2026-08-17T08:00:00+08:00")
    _, second_state = _stage_complete_one_turn_draft(
        tmp_path,
        monkeypatch,
        db_path=db_path,
        activate=False,
    )
    finalize_command({"generation": second_state["generation"]})

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM care_sessions WHERE status='completed'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM fhir_questionnaire_responses"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type='doctor_pathway_activated'"
        ).fetchone()[0] == 1
        authored_dates = [
            json.loads(row[0])["authored"][:10]
            for row in connection.execute(
                "SELECT resource_json FROM fhir_questionnaire_responses "
                "ORDER BY created_at"
            ).fetchall()
        ]
        assert authored_dates == ["2026-08-16", "2026-08-17"]
        observation_days = connection.execute(
            "SELECT substr(effective_time, 1, 10), COUNT(*) "
            "FROM fhir_observations GROUP BY substr(effective_time, 1, 10) "
            "ORDER BY substr(effective_time, 1, 10)"
        ).fetchall()
        assert observation_days == [("2026-08-16", 6), ("2026-08-17", 6)]


@pytest.mark.parametrize(
    ("next_now", "period_end"),
    [
        ("2026-08-16T18:00:00+08:00", "2026-08-20"),
        ("2026-08-18T08:00:00+08:00", "2026-08-20"),
        ("2026-08-17T08:00:00+08:00", "2026-08-16"),
    ],
)
def test_next_daily_checkin_rejects_wrong_or_out_of_plan_date(
    tmp_path, monkeypatch, next_now, period_end
):
    monkeypatch.setenv("CONTINUCARE_SYNTHETIC_NOW", "2026-08-16T08:00:00+08:00")
    db_path = _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()
    confirm_followup_plan(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": "2026-08-16",
            "endDate": period_end,
            "items": [
                {
                    "metricId": item["metricId"],
                    "frequency": "daily",
                }
                for item in proposal["candidates"]
            ],
        }
    )
    _, state = _stage_complete_one_turn_draft(
        tmp_path, monkeypatch, db_path=db_path, activate=False
    )
    finalize_command({"generation": state["generation"]})
    completed = read_competition_demo(db_path)
    before = _sha256(db_path)

    with pytest.raises(CompetitionDemoStartError):
        start_next_competition_checkin(
            db_path,
            expected_generation=completed.generation,
            now=next_now,
        )

    assert _sha256(db_path) == before


@pytest.mark.parametrize(
    ("column", "tampered_value"),
    [
        ("model_provider", "other-provider"),
        ("model_name", "other-model"),
        ("prompt_version", "other-prompt"),
        ("knowledge_release_id", "other-release"),
        ("terminology_catalog_id", "other-catalog"),
        ("terminology_catalog_version", "other-version"),
        ("terminology_catalog_sha256", "0" * 64),
    ],
)
def test_one_final_confirmation_rechecks_exact_run_boundary_in_transaction(
    tmp_path, monkeypatch, column, tampered_value
):
    db_path, state = _stage_complete_one_turn_draft(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE agent_runs SET {column}=?",
            (tampered_value,),
        )

    with pytest.raises(ValueError):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    session = store.get_care_session(progress.session_id)
    assert session.status.value == "in_progress"
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


def test_one_final_confirmation_rechecks_exact_source_catalog_boundary(
    tmp_path, monkeypatch
):
    db_path, state = _stage_complete_one_turn_draft(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT run_id, output_json FROM agent_runs ORDER BY started_at LIMIT 1"
        ).fetchone()
        output = json.loads(row[1])
        output["candidates"][0]["terminology_match"]["source_catalog_version"] = (
            "tampered-source-version"
        )
        connection.execute(
            "UPDATE agent_runs SET output_json=? WHERE run_id=?",
            (json.dumps(output, ensure_ascii=False), row[0]),
        )

    with pytest.raises(ValueError):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


def test_one_final_confirmation_rejects_tampered_provisional_answer_material(
    tmp_path, monkeypatch
):
    db_path, state = _stage_complete_one_turn_draft(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE provisional_answer_contexts SET patient_timezone=? "
            "WHERE link_id='nausea-present' AND status='active'",
            ("Europe/London",),
        )

    with pytest.raises(ValueError, match="草稿内容在生成后发生变化"):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("preferred_zh", "被篡改的名称"),
        (
            "coding_json",
            json.dumps(
                {
                    "system": "http://snomed.info/sct",
                    "code": "tampered-code",
                    "display": "Tampered",
                }
            ),
        ),
        (
            "terminology_match_json",
            json.dumps(
                {
                    "catalog_id": "tampered",
                    "catalog_version": "tampered",
                    "concept_id": "diarrhea",
                    "preferred_zh": "腹泻",
                    "coding": {
                        "system": "http://snomed.info/sct",
                        "code": "62315008",
                    },
                    "target_coding": {
                        "system": "http://snomed.info/sct",
                        "code": "62315008",
                    },
                    "matched_text": "拉肚子",
                    "matched_alias": "拉肚子",
                    "match_method": "tampered",
                    "validation_status": "tampered",
                    "approval_status": "tampered",
                    "target_hospital_validation_required": True,
                },
                ensure_ascii=False,
            ),
        ),
        ("evidence_start", 999),
    ],
)
def test_one_final_confirmation_rejects_tampered_provisional_symptom_material(
    tmp_path, monkeypatch, column, value
):
    symptom_mentions = [
        {
            "symptom_text": "拉肚子",
            "evidence_text": "拉肚子",
            "subject": "patient",
            "temporality": "current",
            "negated": False,
        }
    ]
    db_path, state = _stage_complete_one_turn_draft(
        tmp_path, monkeypatch, symptom_mentions=symptom_mentions
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE provisional_symptom_reports SET {column}=? "
            "WHERE status='active'",
            (value,),
        )

    with pytest.raises(ValueError, match="草稿内容在生成后发生变化"):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


def test_final_transaction_rechecks_run_boundary_after_service_validation(
    tmp_path, monkeypatch
):
    db_path, state = _stage_complete_one_turn_draft(tmp_path, monkeypatch)
    original = SQLiteStore.persist_confirmed_review_bundle

    def tamper_then_persist(store, *args, **kwargs):
        with sqlite3.connect(store.db_path) as connection:
            connection.execute("UPDATE agent_runs SET model_name='tampered-model'")
        return original(store, *args, **kwargs)

    monkeypatch.setattr(
        SQLiteStore, "persist_confirmed_review_bundle", tamper_then_persist
    )
    with pytest.raises(ValueError, match="model/release boundary changed"):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


def test_final_transaction_rechecks_material_digest_after_service_validation(
    tmp_path, monkeypatch
):
    db_path, state = _stage_complete_one_turn_draft(
        tmp_path,
        monkeypatch,
        symptom_mentions=[
            {
                "symptom_text": "拉肚子",
                "evidence_text": "拉肚子",
                "subject": "patient",
                "temporality": "current",
                "negated": False,
            }
        ],
    )
    original = SQLiteStore.persist_confirmed_review_bundle

    def tamper_then_persist(store, *args, **kwargs):
        with sqlite3.connect(store.db_path) as connection:
            connection.execute(
                "UPDATE provisional_symptom_reports "
                "SET preferred_zh='被篡改的名称' WHERE status='active'"
            )
        return original(store, *args, **kwargs)

    monkeypatch.setattr(
        SQLiteStore, "persist_confirmed_review_bundle", tamper_then_persist
    )
    with pytest.raises(ValueError, match="changed before finalization"):
        finalize_command({"generation": state["generation"]})

    progress = read_competition_demo(db_path)
    assert progress.questionnaire_response_count == 0
    assert progress.observation_count == 0
    assert progress.manual_task_count == 0


def test_mixed_candidate_and_clarification_keeps_clarification_authoritative(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    adapter = _stub_mimo_adapter(
        monkeypatch,
        items=[
            {
                "link_id": "nausea-present",
                "answer": True,
                "evidence_text": "现在有恶心",
                "subject": "patient",
                "temporality": "current",
                "negated": False,
            },
            {
                "link_id": "vomiting-count-24h",
                "answer": 2,
                "evidence_text": "吐了2次",
                "subject": "patient",
                "temporality": "unspecified",
                "negated": False,
            },
        ],
    )
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda db_path, **kwargs: submit_patient_chat_turn(
            db_path, model_adapter=adapter, **kwargs
        ),
    )

    submit_chat_command(
        {
            "generation": activated.generation,
            "message": "现在有恶心，吐了2次",
            "syntheticConfirmed": True,
        }
    )

    state = build_patient_mobile_state()
    assert state["kind"] == "clarification"
    store = SQLiteStore(db_path, initialize=False)
    session = store.get_care_session(read_competition_demo(db_path).session_id)
    assert session.answers["nausea-present"] is True
    assert "vomiting-count-24h" not in session.answers
    assert list(store.provisional_action_decisions(session.session_id).values()) == [
        "drafted"
    ]

    resolve_clarification_command(
        {
            "generation": state["generation"],
            "optionId": state["clarification"]["options"][0]["value"],
        }
    )
    updated = build_patient_mobile_state()
    assert updated["kind"] == "collecting"
    assert updated["nextLinkId"] == "nausea-severity"
    assert len(store.provisional_action_decisions(session.session_id)) == 2


def test_soft_semantic_no_match_is_typed_nurse_handoff_and_keeps_pathway(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    before_question = build_patient_mobile_state()["nextQuestion"]

    after = submit_patient_chat_turn(
        db_path,
        expected_generation=activated.generation,
        message_text="今天有点说不清楚",
        synthetic_confirmed=True,
        model_adapter=_stub_mimo_adapter(monkeypatch, items=[]),
    )

    assert after.stage.value == "patient_collecting"
    assert after.run_id is None
    assert after.generation != activated.generation
    store = SQLiteStore(db_path, initialize=False)
    session = store.get_care_session(after.session_id)
    assert session.answers == {}
    assert store.list_active_answer_contexts(after.session_id) == []
    projection = read_supplemental_reports(db_path, session_id=after.session_id)
    assert projection.integrity_issue is None
    assert len(projection.reports) == 1
    report = projection.reports[0]
    assert report.report_kind == "semantic_handoff"
    assert report.handoff_reason_code == "no_structured_match"
    assert report.structured_items == ()
    assert report.questionnaire_response_id is None
    assert report.observation_ids == ()
    assert report.status == "requested"
    with sqlite3.connect(db_path) as connection:
        counts = {
            "runs": connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
            "audits": connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='patient_semantic_handoff_requested'"
            ).fetchone()[0],
            "responses": connection.execute(
                "SELECT COUNT(*) FROM fhir_questionnaire_responses"
            ).fetchone()[0],
            "observations": connection.execute(
                "SELECT COUNT(*) FROM fhir_observations"
            ).fetchone()[0],
            "alerts": connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
        }
    assert counts == {"runs": 1, "audits": 1, "responses": 0, "observations": 0, "alerts": 0}
    patient_state = build_patient_mobile_state()
    assert patient_state["kind"] == "collecting"
    assert patient_state["nextQuestion"] == before_question
    assert any("原话已保留并进入护士人工复核" in item.get("text", "") for item in patient_state["history"])
    nurse_state = build_nurse_portal_state()
    assert nurse_state["kind"] == "empty"
    assert nurse_state["supplementalReports"][0]["reportKind"] == "semantic_handoff"
    assert nurse_state["supplementalReports"][0]["clinicalAssessment"] == "not_assessed"

    captured = {}
    adapter = _stub_mimo_adapter(
        monkeypatch,
        items=[
            {
                "link_id": "nausea-present",
                "answer": True,
                "evidence_text": "有恶心",
                "subject": "patient",
                "temporality": "unspecified",
                "negated": False,
            }
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "continucare.patient_mobile.submit_patient_chat_turn",
        lambda db_path, **kwargs: submit_patient_chat_turn(
            db_path, model_adapter=adapter, **kwargs
        ),
    )
    submit_chat_command(
        {
            "generation": after.generation,
            "message": "有恶心",
            "syntheticConfirmed": True,
        }
    )
    candidate_state = build_patient_mobile_state()
    assert candidate_state["kind"] == "collecting"
    assert candidate_state["nextLinkId"] == "nausea-severity"
    assert any(
        item.get("kind") == "draft_record" for item in candidate_state["history"]
    )
    assert '"link_id":"vomiting-count-24h"' not in captured["payload"]["messages"][0]["content"]


def test_same_generation_soft_handoff_calls_mimo_and_persists_once(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    adapter = _stub_mimo_adapter(monkeypatch, items=[])

    def invoke():
        try:
            submit_patient_chat_turn(
                db_path,
                expected_generation=activated.generation,
                message_text="今天有点说不清楚",
                synthetic_confirmed=True,
                model_adapter=adapter,
            )
            return "applied"
        except CompetitionDemoConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: invoke(), range(2)))

    progress = read_competition_demo(db_path)
    projection = read_supplemental_reports(db_path, session_id=progress.session_id)
    assert sorted(outcomes) == ["applied", "conflict"]
    assert len(projection.reports) == 1
    assert len(SQLiteStore(db_path, initialize=False).list_agent_runs(progress.session_id)) == 1


def test_provider_failure_is_hard_and_creates_no_nurse_handoff(
    tmp_path, monkeypatch
):
    db_path = _configured_db(tmp_path, monkeypatch)
    activated = activate_competition_plan(db_path, expected_generation=None)
    before = _sha256(db_path)

    with pytest.raises(CompetitionDemoStartError):
        submit_patient_chat_turn(
            db_path,
            expected_generation=activated.generation,
            message_text="今天有点说不清楚",
            synthetic_confirmed=True,
            model_adapter=_stub_mimo_adapter(monkeypatch, items=[], fail=True),
        )

    assert _sha256(db_path) == before
    projection = read_supplemental_reports(db_path, session_id=activated.session_id)
    assert projection.reports == ()
    assert SQLiteStore(db_path, initialize=False).list_agent_runs(activated.session_id) == []


def test_local_api_rejects_cross_origin_bad_host_and_non_json(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    state_response = asyncio.run(
        api_state(_request("GET", "/api/state", headers={"host": "127.0.0.1:8510"}))
    )
    endpoint = _command(submit_chat_command)
    body = json.dumps({"generation": "missing", "message": "合成回答"}).encode()
    bad_origin = asyncio.run(
        endpoint(
            _request(
                "POST",
                "/api/chat",
                body=body,
                headers={
                    "host": "127.0.0.1:8510",
                    "origin": "https://evil.example",
                    "x-continucare-csrf": CSRF_TOKEN,
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                },
            )
        )
    )
    bad_host = asyncio.run(
        endpoint(
            _request(
                "POST",
                "/api/chat",
                body=body,
                headers={
                    "host": "evil.example",
                    "origin": "http://evil.example",
                    "x-continucare-csrf": CSRF_TOKEN,
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                },
            )
        )
    )
    non_json = asyncio.run(
        endpoint(
            _request(
                "POST",
                "/api/chat",
                body="合成回答".encode(),
                headers={
                    "host": "127.0.0.1:8510",
                    "origin": "http://127.0.0.1:8510",
                    "x-continucare-csrf": CSRF_TOKEN,
                    "content-type": "text/plain",
                },
            )
        )
    )

    assert bad_origin.status_code == 422
    assert bad_host.status_code == 422
    assert non_json.status_code == 422
    assert state_response.headers["cache-control"].startswith("no-store")
    assert state_response.headers["referrer-policy"] == "no-referrer"


def test_unknown_api_route_never_falls_through_to_spa(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    response = asyncio.run(
        spa(
            _request(
                "GET",
                "/api/not-a-route",
                headers={"host": "127.0.0.1:8510"},
                path_params={"path": "api/not-a-route"},
            )
        )
    )

    assert response.status_code == 404
    assert json.loads(response.body)["error"]["code"] == "not_found"
    assert "text/html" not in response.headers.get("content-type", "")


def test_nurse_route_serves_server_state_and_uses_same_origin_write_guard(
    tmp_path, monkeypatch
):
    _configured_db(tmp_path, monkeypatch)

    status, headers, body = _asgi_request(
        "GET",
        "/api/nurse/state",
        headers={"host": "127.0.0.1:8510"},
    )
    state = json.loads(body)["data"]
    assert status == 200
    assert state["kind"] == "waiting"
    assert headers["x-continucare-csrf"]

    payload = json.dumps(
        {"generation": "stale", "taskId": "missing-task"}
    ).encode()
    status, _, body = _asgi_request(
        "POST",
        "/api/nurse/tasks/acknowledge",
        body=payload,
        headers={
            "host": "127.0.0.1:8510",
            "origin": "http://127.0.0.1:8510",
            "x-continucare-csrf": headers["x-continucare-csrf"],
            "content-type": "application/json",
            "content-length": str(len(payload)),
        },
    )
    assert status == 409
    assert json.loads(body)["error"]["code"] == "state_conflict"
