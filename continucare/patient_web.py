"""Local-only Starlette host for the role-separated React web clients."""

from __future__ import annotations

import hmac
import json
import secrets
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from continucare.patient_mobile import (
    PatientMobileBoundaryError,
    build_patient_mobile_state,
    explicit_unknown_command,
    finalize_command,
    remove_additional_report_command,
    resolve_candidates_command,
    resolve_clarification_command,
    resolve_supplemental_command,
    submit_chat_command,
)
from continucare.nurse_portal import (
    NursePortalBoundaryError,
    acknowledge_nurse_task_command,
    approve_nurse_draft_command,
    build_nurse_portal_state,
    close_nurse_task_command,
    record_nurse_outcome_command,
    review_nurse_supplemental_command,
    start_nurse_task_command,
)
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoStartError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "patient-web" / "dist"
CSRF_TOKEN = secrets.token_urlsafe(32)
MAX_BODY_BYTES = 16_384
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _security_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


def _host(request: Request) -> str:
    raw = request.headers.get("host", "")
    return raw.rsplit(":", 1)[0].strip("[]").lower()


def _same_origin(request: Request) -> bool:
    expected_host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin or referer
    if not candidate:
        return False
    parsed = urlsplit(candidate)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host


async def _json_payload(request: Request) -> dict[str, Any]:
    if _host(request) not in ALLOWED_HOSTS:
        raise PatientMobileBoundaryError("请求主机不受信任")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/json":
        raise PatientMobileBoundaryError("写操作只接受 JSON")
    if not _same_origin(request):
        raise PatientMobileBoundaryError("跨来源请求已拒绝")
    supplied = request.headers.get("x-continucare-csrf", "")
    if not hmac.compare_digest(supplied, CSRF_TOKEN):
        raise PatientMobileBoundaryError("页面令牌已经失效，请刷新")
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise PatientMobileBoundaryError("请求内容过长")
        except ValueError as exc:
            raise PatientMobileBoundaryError("请求长度无效") from exc
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise PatientMobileBoundaryError("请求内容过长")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatientMobileBoundaryError("JSON 内容无效") from exc
    if not isinstance(payload, dict):
        raise PatientMobileBoundaryError("JSON 内容必须是对象")
    return payload


async def api_state(request: Request) -> Response:
    if _host(request) not in ALLOWED_HOSTS:
        return _security_headers(JSONResponse({"error": {"code": "untrusted_host", "message": "请求主机不受信任"}}, status_code=400))
    state = await run_in_threadpool(build_patient_mobile_state)
    response = JSONResponse({"data": state})
    response.headers["X-ContinuCare-CSRF"] = CSRF_TOKEN
    return _security_headers(response)


async def api_nurse_state(request: Request) -> Response:
    if _host(request) not in ALLOWED_HOSTS:
        return _security_headers(JSONResponse({"error": {"code": "untrusted_host", "message": "请求主机不受信任"}}, status_code=400))
    selected = request.query_params.get("taskId")
    if selected is not None and (not selected.strip() or len(selected) > 256):
        return _security_headers(JSONResponse({"error": {"code": "invalid_task", "message": "人工复核任务无效"}}, status_code=422))
    state = await run_in_threadpool(
        build_nurse_portal_state,
        selected_task_id=selected,
    )
    response = JSONResponse({"data": state})
    response.headers["X-ContinuCare-CSRF"] = CSRF_TOKEN
    return _security_headers(response)


def _command(handler: Callable[[dict[str, Any]], None]):
    async def endpoint(request: Request) -> Response:
        try:
            payload = await _json_payload(request)
            await run_in_threadpool(handler, payload)
        except CompetitionDemoConflict:
            return _security_headers(JSONResponse({"error": {"code": "state_conflict", "message": "页面状态已经变化，请刷新后继续"}}, status_code=409))
        except CompetitionDemoStartError:
            return _security_headers(JSONResponse({"error": {"code": "mimo_rejected", "message": "这轮整理没有完成；请重试。已有记录未改变，这句话也没有进入护士队列"}}, status_code=422))
        except (PatientMobileBoundaryError, NursePortalBoundaryError) as exc:
            return _security_headers(JSONResponse({"error": {"code": "boundary_rejected", "message": str(exc)}}, status_code=422))
        except ValueError:
            return _security_headers(JSONResponse({"error": {"code": "invalid_action", "message": "当前操作无效，请刷新后重试"}}, status_code=422))
        except Exception:
            return _security_headers(JSONResponse({"error": {"code": "internal_error", "message": "操作未完成，原记录保持不变"}}, status_code=500))
        return _security_headers(JSONResponse({"ok": True}))

    return endpoint


async def spa(request: Request) -> Response:
    if _host(request) not in ALLOWED_HOSTS:
        return _security_headers(Response("Bad Request", status_code=400))
    path = request.path_params.get("path", "")
    if path.startswith("api/"):
        return _security_headers(JSONResponse({"error": {"code": "not_found", "message": "API 路由不存在"}}, status_code=404))
    candidate = (DIST_DIR / path).resolve() if path else DIST_DIR / "index.html"
    if path and candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
        return _security_headers(FileResponse(candidate))
    index = DIST_DIR / "index.html"
    if not index.is_file():
        return _security_headers(Response("Patient web build is missing. Run npm run build in patient-web.", status_code=503))
    return _security_headers(FileResponse(index))


routes = [
    Route("/api/state", api_state, methods=["GET"]),
    Route("/api/nurse/state", api_nurse_state, methods=["GET"]),
    Route("/api/nurse/tasks/acknowledge", _command(acknowledge_nurse_task_command), methods=["POST"]),
    Route("/api/nurse/tasks/start", _command(start_nurse_task_command), methods=["POST"]),
    Route("/api/nurse/tasks/outcome", _command(record_nurse_outcome_command), methods=["POST"]),
    Route("/api/nurse/tasks/approve-draft", _command(approve_nurse_draft_command), methods=["POST"]),
    Route("/api/nurse/tasks/reject", _command(lambda payload: close_nurse_task_command(payload, action="reject")), methods=["POST"]),
    Route("/api/nurse/tasks/cancel", _command(lambda payload: close_nurse_task_command(payload, action="cancel")), methods=["POST"]),
    Route("/api/nurse/supplemental/review", _command(review_nurse_supplemental_command), methods=["POST"]),
    Route("/api/chat", _command(submit_chat_command), methods=["POST"]),
    Route("/api/candidates/resolve", _command(resolve_candidates_command), methods=["POST"]),
    Route("/api/clarification/resolve", _command(resolve_clarification_command), methods=["POST"]),
    Route("/api/explicit-unknown", _command(explicit_unknown_command), methods=["POST"]),
    Route("/api/finalize", _command(finalize_command), methods=["POST"]),
    Route(
        "/api/draft-reports/remove",
        _command(remove_additional_report_command),
        methods=["POST"],
    ),
    Route("/api/supplemental/resolve", _command(resolve_supplemental_command), methods=["POST"]),
    Route("/{path:path}", spa, methods=["GET"]),
]

app = Starlette(debug=False, routes=routes)


def main() -> None:
    uvicorn.run(
        "continucare.patient_web:app",
        host="127.0.0.1",
        port=8510,
        workers=1,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
