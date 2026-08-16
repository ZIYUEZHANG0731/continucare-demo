from __future__ import annotations

import asyncio
import json

import pytest

from continucare.db import reset_demo
from continucare.doctor_planning import (
    build_followup_plan_proposal,
    confirm_followup_plan,
)
from continucare.doctor_portal import (
    DoctorPortalBoundaryError,
    build_doctor_portal_state,
)
from continucare.doctor_web import app


def _configured_db(tmp_path, monkeypatch):
    db_path = tmp_path / "doctor-web.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.delenv("CONTINUCARE_DOCTOR_ACCESS_KEY", raising=False)
    monkeypatch.delenv("CONTINUCARE_DOCTOR_SESSION_SECRET", raising=False)
    monkeypatch.delenv("CONTINUCARE_DOCTOR_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("CONTINUCARE_DOCTOR_PATIENT_IDS", raising=False)
    reset_demo(db_path)
    return db_path


def _asgi_request(method, path, *, headers=None, body=b""):
    route_path, _, query = path.partition("?")
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
        "path": route_path,
        "raw_path": route_path.encode("ascii"),
        "query_string": query.encode("ascii"),
        "headers": encoded_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8520),
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
    response_headers = [
        (key.decode("latin-1").lower(), value.decode("latin-1"))
        for key, value in start["headers"]
    ]
    return start["status"], response_headers, response_body


def _header(headers, name):
    return next(value for key, value in headers if key == name)


def test_portal_state_is_patient_scoped_and_handles_empty_metrics(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)

    state = build_doctor_portal_state()

    assert state["version"] == 1
    assert state["patient"]["patientId"] == "P-DEMO-001"
    assert state["overview"]["recordDayCount"] == 0
    assert state["overview"]["missingMetrics"]
    assert state["metrics"]
    assert "resource_json" not in json.dumps(state)


def test_portal_rejects_patient_outside_explicit_allowlist(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    monkeypatch.setenv("CONTINUCARE_DOCTOR_PATIENT_IDS", "P-OTHER")

    try:
        build_doctor_portal_state("P-DEMO-001")
    except DoctorPortalBoundaryError as exc:
        assert "授权范围" in str(exc)
    else:
        raise AssertionError("out-of-scope patient was not rejected")


def test_local_dashboard_api_is_read_only_and_security_hardened(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)

    status, headers, body = _asgi_request(
        "GET", "/api/doctor/dashboard", headers={"Host": "127.0.0.1:8520"}
    )

    assert status == 200
    assert json.loads(body)["data"]["patient"]["patientId"] == "P-DEMO-001"
    assert _header(headers, "x-frame-options") == "DENY"
    assert "default-src 'self'" in _header(headers, "content-security-policy")
    assert _header(headers, "cache-control") == "no-store, max-age=0"


def test_external_host_requires_login_and_accepts_signed_session(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    monkeypatch.setenv("CONTINUCARE_DOCTOR_ALLOWED_HOSTS", "doctor.example.org")
    monkeypatch.setenv("CONTINUCARE_DOCTOR_ACCESS_KEY", "doctor-test-key")
    monkeypatch.setenv("CONTINUCARE_DOCTOR_SESSION_SECRET", "s" * 48)

    status, _, body = _asgi_request(
        "GET", "/api/doctor/dashboard", headers={"Host": "doctor.example.org"}
    )
    assert status == 401
    assert json.loads(body)["error"]["code"] == "authentication_required"

    payload = json.dumps({"accessKey": "doctor-test-key"}).encode()
    status, headers, _ = _asgi_request(
        "POST",
        "/api/session",
        headers={
            "Host": "doctor.example.org",
            "Origin": "https://doctor.example.org",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
        body=payload,
    )
    assert status == 200
    cookie = _header(headers, "set-cookie").split(";", 1)[0]

    status, _, body = _asgi_request(
        "GET",
        "/api/doctor/dashboard",
        headers={"Host": "doctor.example.org", "Cookie": cookie},
    )
    assert status == 200
    assert json.loads(body)["data"]["overview"]["title"] == "随访小结"


def test_external_host_fails_closed_when_secrets_are_missing(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    monkeypatch.setenv("CONTINUCARE_DOCTOR_ALLOWED_HOSTS", "doctor.example.org")

    status, _, body = _asgi_request(
        "GET", "/api/doctor/dashboard", headers={"Host": "doctor.example.org"}
    )

    assert status == 503
    assert json.loads(body)["error"]["code"] == "server_not_configured"


def test_untrusted_host_is_rejected_before_data_access(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)

    status, _, body = _asgi_request(
        "GET", "/api/doctor/dashboard", headers={"Host": "attacker.example"}
    )

    assert status == 400
    assert json.loads(body)["error"]["code"] == "untrusted_host"


def test_planning_proposal_uses_minimal_ehr_context_and_governed_metrics(
    tmp_path, monkeypatch
):
    _configured_db(tmp_path, monkeypatch)

    proposal = build_followup_plan_proposal()

    assert proposal["ehr"]["snapshotId"] == "ehr-synthetic-p-demo-001-v1"
    assert proposal["knowledge"]["releaseId"] == "cn-glp1-l1-v1.0.3"
    assert proposal["knowledge"]["productionClinicalRuntimeEligible"] is False
    assert proposal["activation"]["mode"] == "synthetic_demo"
    assert proposal["proposalVersion"] == 3
    assert proposal["knowledge"]["goalRuleSetId"] == "continucare-followup-goals-v1.0.0"
    assert len(proposal["candidates"]) == 6
    assert {item["metricId"] for item in proposal["candidates"]} == {
        "body_weight",
        "nausea_present_now",
        "nausea_severity_current",
        "vomiting_count_24h",
        "fluid_intake_24h_estimated",
        "abdominal_pain_present_now",
    }
    weight = next(item for item in proposal["candidates"] if item["metricId"] == "body_weight")
    assert weight["categoryId"] == "core_outcome"
    assert weight["required"] is True
    assert weight["defaultFrequency"] == "weekly"
    assert weight["unit"] == "kg"
    assert weight["evidence"][0]["sourceId"] == "nhc-obesity-guideline-2024"
    nausea = next(
        item
        for item in proposal["candidates"]
        if item["metricId"] == "nausea_severity_current"
    )
    assert nausea["priority"] == "重点建议"
    assert "finding-recent-nausea" in nausea["contextRefs"]
    assert nausea["evidence"]
    assert all(item["claimId"] for item in nausea["evidence"])
    assert nausea["recordPoint"]["recordPointId"] == "nausea"
    assert nausea["recordPoint"]["fields"][1]["enableWhen"]["answer"] is True
    serialized = json.dumps(proposal, ensure_ascii=False)
    assert "身份证号码" not in serialized
    assert "resource_json" not in serialized


def test_confirm_plan_rebuilds_server_evidence_and_rejects_stale_plan_version(
    tmp_path, monkeypatch
):
    _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()
    payload = {
        "patientId": proposal["patientId"],
        "proposalId": proposal["proposalId"],
        "startDate": proposal["period"]["startDate"],
        "endDate": proposal["period"]["endDate"],
        "items": [
            {"metricId": item["metricId"], "frequency": "daily"}
            for item in proposal["candidates"][:3]
        ],
    }

    first = confirm_followup_plan(payload)
    refreshed_proposal = build_followup_plan_proposal()
    stale_rejected = False
    try:
        confirm_followup_plan(payload)
    except DoctorPortalBoundaryError:
        stale_rejected = True
    assert stale_rejected is True
    second = confirm_followup_plan(
        {**payload, "proposalId": refreshed_proposal["proposalId"]}
    )
    refreshed = build_followup_plan_proposal()

    assert first["planVersion"] == 1
    assert second["planVersion"] == 2
    assert second["planId"] == first["planId"]
    assert refreshed["currentPlan"]["planVersion"] == 2
    assert all(item["evidenceClaimIds"] for item in second["items"])


def test_confirm_plan_rejects_client_injected_metric(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()
    payload = {
        "patientId": proposal["patientId"],
        "proposalId": proposal["proposalId"],
        "startDate": proposal["period"]["startDate"],
        "endDate": proposal["period"]["endDate"],
        "items": [{"metricId": "client_injected", "frequency": "daily"}],
    }

    try:
        confirm_followup_plan(payload)
    except DoctorPortalBoundaryError as exc:
        assert "推荐范围" in str(exc)
    else:
        raise AssertionError("client-injected metric was accepted")


def test_confirm_plan_rejects_different_frequencies_inside_one_record_point(
    tmp_path, monkeypatch
):
    _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()

    with pytest.raises(DoctorPortalBoundaryError, match="同一记录要点只能设置一个频率"):
        confirm_followup_plan(
            {
                "patientId": proposal["patientId"],
                "proposalId": proposal["proposalId"],
                "startDate": proposal["period"]["startDate"],
                "endDate": proposal["period"]["endDate"],
                "items": [
                    {"metricId": "body_weight", "frequency": "weekly"},
                    {"metricId": "nausea_present_now", "frequency": "daily"},
                    {
                        "metricId": "nausea_severity_current",
                        "frequency": "weekly",
                    },
                ],
            }
        )


def test_confirm_plan_requires_core_metrics_and_accepts_versioned_custom_metric(
    tmp_path, monkeypatch
):
    _configured_db(tmp_path, monkeypatch)
    proposal = build_followup_plan_proposal()
    without_weight = {
        "patientId": proposal["patientId"],
        "proposalId": proposal["proposalId"],
        "startDate": proposal["period"]["startDate"],
        "endDate": proposal["period"]["endDate"],
        "items": [
            {"metricId": "nausea_present_now", "frequency": "daily"}
        ],
    }
    try:
        confirm_followup_plan(without_weight)
    except DoctorPortalBoundaryError as exc:
        assert "体重" in str(exc)
    else:
        raise AssertionError("required body weight metric was omitted")

    payload = {
        **without_weight,
        "items": [
            {"metricId": "body_weight", "frequency": "weekly"},
            {"metricId": "nausea_present_now", "frequency": "daily"},
            {"metricId": "nausea_severity_current", "frequency": "daily"},
            {
                "isCustom": True,
                "displayName": "睡眠时长",
                "clinicalIntent": "记录昨夜总睡眠时长",
                "dataType": "quantity",
                "unit": "小时",
                "frequency": "daily",
            },
        ],
    }
    saved = confirm_followup_plan(payload)
    custom = next(item for item in saved["items"] if item["sourceType"] == "doctor_custom")
    assert custom["metricId"].startswith("custom_")
    assert custom["displayName"] == "睡眠时长"
    assert custom["unit"] == "小时"
    assert saved["goalRuleSetId"] == "continucare-followup-goals-v1.0.0"
    refreshed = build_followup_plan_proposal()
    assert refreshed["currentPlan"]["items"][-1]["metricId"] == custom["metricId"]


def test_planning_api_and_same_origin_plan_confirmation(tmp_path, monkeypatch):
    _configured_db(tmp_path, monkeypatch)
    status, _, body = _asgi_request(
        "GET", "/api/doctor/planning", headers={"Host": "127.0.0.1:8520"}
    )
    assert status == 200
    proposal = json.loads(body)["data"]
    payload = json.dumps(
        {
            "patientId": proposal["patientId"],
            "proposalId": proposal["proposalId"],
            "startDate": proposal["period"]["startDate"],
            "endDate": proposal["period"]["endDate"],
            "items": [
                {"metricId": item["metricId"], "frequency": "daily"}
                for item in proposal["candidates"][:3]
            ],
        }
    ).encode()
    status, headers, body = _asgi_request(
        "POST",
        "/api/doctor/plans",
        headers={
            "Host": "127.0.0.1:8520",
            "Origin": "http://127.0.0.1:8520",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
        body=payload,
    )

    assert status == 201
    assert json.loads(body)["data"]["status"] == "confirmed"
    assert _header(headers, "cache-control") == "no-store, max-age=0"
