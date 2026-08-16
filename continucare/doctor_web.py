"""Standalone Starlette host for the ContinuCare doctor React application."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from continucare.doctor_portal import (
    DoctorPortalBoundaryError,
    build_doctor_portal_state,
    list_doctor_patients,
)
from continucare.doctor_planning import (
    build_followup_plan_proposal,
    confirm_followup_plan,
)
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoStartError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "doctor-web" / "dist"
COOKIE_NAME = "continucare_doctor_session"
SESSION_SECONDS = 8 * 60 * 60
MAX_BODY_BYTES = 16_384
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_SESSION_SECRET = secrets.token_urlsafe(48)
FAILED_LOGINS: dict[str, list[float]] = {}


def _allowed_hosts() -> set[str]:
    configured = {
        item.strip().lower()
        for item in os.getenv("CONTINUCARE_DOCTOR_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    return configured or LOCAL_HOSTS


def _host(request: Request) -> str:
    raw = request.headers.get("host", "")
    if raw.startswith("[") and "]" in raw:
        return raw[1 : raw.index("]")].lower()
    return raw.rsplit(":", 1)[0].lower()


def _trusted_host(request: Request) -> bool:
    return _host(request) in _allowed_hosts()


def _same_origin(request: Request) -> bool:
    expected_host = request.headers.get("host", "").lower()
    candidate = request.headers.get("origin") or request.headers.get("referer")
    if not candidate:
        return False
    parsed = urlsplit(candidate)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host


def _security_headers(response: Response, *, api: bool = False) -> Response:
    response.headers["Cache-Control"] = "no-store, max-age=0" if api else "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


def _error(code: str, message: str, status_code: int) -> Response:
    return _security_headers(
        JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code),
        api=True,
    )


def _access_key() -> str:
    return os.getenv("CONTINUCARE_DOCTOR_ACCESS_KEY", "")


def _session_secret(request: Request) -> str | None:
    configured = os.getenv("CONTINUCARE_DOCTOR_SESSION_SECRET", "")
    if configured:
        return configured
    return LOCAL_SESSION_SECRET if _host(request) in LOCAL_HOSTS else None


def _production_configured(request: Request) -> bool:
    if _host(request) in LOCAL_HOSTS:
        return True
    return bool(_access_key() and os.getenv("CONTINUCARE_DOCTOR_SESSION_SECRET"))


def _sign_session(request: Request, expires_at: int) -> str:
    secret = _session_secret(request)
    if secret is None:
        raise RuntimeError("doctor session secret is not configured")
    payload = str(expires_at)
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def _authenticated(request: Request) -> bool:
    if not _access_key() and _host(request) in LOCAL_HOSTS:
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        expires_text, supplied = token.split(".", 1)
        expires_at = int(expires_text)
    except (TypeError, ValueError):
        return False
    if expires_at <= int(time.time()):
        return False
    try:
        expected = _sign_session(request, expires_at).split(".", 1)[1]
    except RuntimeError:
        return False
    return hmac.compare_digest(supplied, expected)


async def _json_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/json":
        raise DoctorPortalBoundaryError("请求只接受 JSON")
    if not _same_origin(request):
        raise DoctorPortalBoundaryError("跨来源请求已拒绝")
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise DoctorPortalBoundaryError("请求内容过长")
        except ValueError as exc:
            raise DoctorPortalBoundaryError("请求长度无效") from exc
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise DoctorPortalBoundaryError("请求内容过长")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorPortalBoundaryError("JSON 内容无效") from exc
    if not isinstance(payload, dict):
        raise DoctorPortalBoundaryError("JSON 内容必须是对象")
    return payload


def _login_limited(request: Request) -> bool:
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    recent = [item for item in FAILED_LOGINS.get(key, []) if now - item < 300]
    FAILED_LOGINS[key] = recent
    return len(recent) >= 5


def _record_failed_login(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    FAILED_LOGINS.setdefault(key, []).append(time.monotonic())


async def health(_request: Request) -> Response:
    return _security_headers(JSONResponse({"status": "ok"}), api=True)


async def create_session(request: Request) -> Response:
    if not _trusted_host(request):
        return _error("untrusted_host", "请求主机不受信任", 400)
    if not _production_configured(request):
        return _error("server_not_configured", "医生端登录尚未完成部署配置", 503)
    if _login_limited(request):
        return _error("too_many_attempts", "登录尝试过多，请稍后再试", 429)
    try:
        payload = await _json_payload(request)
    except DoctorPortalBoundaryError as exc:
        return _error("invalid_request", str(exc), 422)
    supplied = str(payload.get("accessKey") or "")
    expected = _access_key()
    if not expected or not hmac.compare_digest(supplied, expected):
        _record_failed_login(request)
        return _error("invalid_credentials", "访问密钥不正确", 401)
    if request.client:
        FAILED_LOGINS.pop(request.client.host, None)
    expires_at = int(time.time()) + SESSION_SECONDS
    response = JSONResponse({"ok": True, "expiresAt": expires_at})
    secure = request.url.scheme == "https" or os.getenv(
        "CONTINUCARE_DOCTOR_SECURE_COOKIE", ""
    ).lower() in {"1", "true", "yes"}
    response.set_cookie(
        COOKIE_NAME,
        _sign_session(request, expires_at),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return _security_headers(response, api=True)


def _authorized(request: Request) -> Response | None:
    if not _trusted_host(request):
        return _error("untrusted_host", "请求主机不受信任", 400)
    if not _production_configured(request):
        return _error("server_not_configured", "医生端尚未完成部署配置", 503)
    if not _authenticated(request):
        return _error("authentication_required", "请先登录医生工作台", 401)
    return None


async def api_patients(request: Request) -> Response:
    rejected = _authorized(request)
    if rejected is not None:
        return rejected
    try:
        patients = await run_in_threadpool(list_doctor_patients)
    except (OSError, ValueError):
        return _error("data_unavailable", "患者列表暂时不可读取", 503)
    return _security_headers(JSONResponse({"data": patients}), api=True)


async def api_dashboard(request: Request) -> Response:
    rejected = _authorized(request)
    if rejected is not None:
        return rejected
    patient_id = request.query_params.get("patientId") or "P-DEMO-001"
    try:
        state = await run_in_threadpool(build_doctor_portal_state, patient_id)
    except DoctorPortalBoundaryError as exc:
        return _error("patient_scope_rejected", str(exc), 403)
    except (LookupError, OSError, ValueError):
        return _error("data_unavailable", "医生端数据暂时不可读取", 503)
    return _security_headers(JSONResponse({"data": state}), api=True)


async def api_planning(request: Request) -> Response:
    rejected = _authorized(request)
    if rejected is not None:
        return rejected
    patient_id = request.query_params.get("patientId") or "P-DEMO-001"
    try:
        proposal = await run_in_threadpool(
            build_followup_plan_proposal, patient_id
        )
    except DoctorPortalBoundaryError as exc:
        return _error("planning_scope_rejected", str(exc), 403)
    except (LookupError, OSError, ValueError):
        return _error("data_unavailable", "随访规划数据暂时不可读取", 503)
    return _security_headers(JSONResponse({"data": proposal}), api=True)


async def api_confirm_plan(request: Request) -> Response:
    rejected = _authorized(request)
    if rejected is not None:
        return rejected
    try:
        payload = await _json_payload(request)
        plan = await run_in_threadpool(confirm_followup_plan, payload)
    except CompetitionDemoConflict:
        return _error("shared_state_changed", "共享随访状态已经变化，请刷新后重新确认", 409)
    except CompetitionDemoStartError:
        return _error("activation_unavailable", "方案与患者随访暂时无法一起启用", 503)
    except DoctorPortalBoundaryError as exc:
        return _error("plan_rejected", str(exc), 422)
    except (LookupError, OSError, ValueError):
        return _error("data_unavailable", "随访方案暂时无法保存", 503)
    return _security_headers(JSONResponse({"data": plan}, status_code=201), api=True)


async def spa(request: Request) -> Response:
    if not _trusted_host(request):
        return _security_headers(Response("Bad Request", status_code=400))
    path = request.path_params.get("path", "")
    if path.startswith("api/"):
        return _error("not_found", "API 路由不存在", 404)
    candidate = (DIST_DIR / path).resolve() if path else DIST_DIR / "index.html"
    if path and candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
        return _security_headers(FileResponse(candidate))
    index = DIST_DIR / "index.html"
    if not index.is_file():
        return _security_headers(
            Response("Doctor web build is missing. Run npm run build in doctor-web.", status_code=503)
        )
    return _security_headers(FileResponse(index))


routes = [
    Route("/healthz", health, methods=["GET"]),
    Route("/api/session", create_session, methods=["POST"]),
    Route("/api/doctor/patients", api_patients, methods=["GET"]),
    Route("/api/doctor/dashboard", api_dashboard, methods=["GET"]),
    Route("/api/doctor/planning", api_planning, methods=["GET"]),
    Route("/api/doctor/plans", api_confirm_plan, methods=["POST"]),
    Route("/{path:path}", spa, methods=["GET"]),
]

app = Starlette(debug=False, routes=routes)


def main() -> None:
    uvicorn.run(
        "continucare.doctor_web:app",
        host=os.getenv("CONTINUCARE_DOCTOR_HOST", "127.0.0.1"),
        port=int(os.getenv("CONTINUCARE_DOCTOR_PORT", "8520")),
        workers=1,
        reload=False,
        access_log=False,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
