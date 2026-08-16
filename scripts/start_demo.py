"""Start the local ContinuCare patient, nurse, and doctor web surfaces."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

from continucare.config import get_settings, load_local_environment
from continucare.care_agent.model_api import SemanticModelConfig


PATIENT_URL = "http://127.0.0.1:8510/"
NURSE_URL = "http://127.0.0.1:8510/nurse"
DOCTOR_URL = "http://127.0.0.1:8520/"
READINESS_URLS = (
    ("患者 / 护士服务", "http://127.0.0.1:8510/api/state"),
    ("医生服务", "http://127.0.0.1:8520/healthz"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="启动共享同一 SQLite 数据库的 ContinuCare 三角色网页。"
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="服务就绪后在默认浏览器中打开医生、患者和护士页面。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查前端构建产物和本地配置，不启动服务。",
    )
    return parser


def _missing_builds() -> list[Path]:
    return [
        path
        for path in (
            PROJECT_ROOT / "patient-web" / "dist" / "index.html",
            PROJECT_ROOT / "doctor-web" / "dist" / "index.html",
        )
        if not path.is_file()
    ]


def _wait_until_ready(processes: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + 30
    pending = dict(READINESS_URLS)
    while pending and time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"网页服务提前退出（exit {process.returncode}）")
        for label, url in list(pending.items()):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status < 500:
                        pending.pop(label)
            except (OSError, urllib.error.URLError):
                pass
        if pending:
            time.sleep(0.2)
    if pending:
        raise RuntimeError(f"网页服务未在 30 秒内就绪：{'、'.join(pending)}")


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        remaining = max(0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    args = _parser().parse_args()
    os.chdir(PROJECT_ROOT)
    load_local_environment(PROJECT_ROOT / ".env")

    missing = _missing_builds()
    if missing:
        print("缺少前端构建产物：", file=sys.stderr)
        for path in missing:
            print(f"- {path.relative_to(PROJECT_ROOT)}", file=sys.stderr)
        print(
            "请先运行：npm --prefix patient-web ci && npm --prefix patient-web run build",
            file=sys.stderr,
        )
        print(
            "          npm --prefix doctor-web ci && npm --prefix doctor-web run build",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()
    model_config = SemanticModelConfig.from_environment()
    print(f"配置检查通过；共享数据库：{settings.db_path}")
    if model_config.configured:
        print(f"患者语义模型：{model_config.provider} / {model_config.model_name}")
    else:
        print(
            "患者语义模型：未完整配置；网页可以启动，但患者自然语言发送将被禁用。",
            file=sys.stderr,
        )
    print(f"医生端：{DOCTOR_URL}")
    print(f"患者端：{PATIENT_URL}")
    print(f"护士端：{NURSE_URL}")
    if args.check:
        return 0

    environment = os.environ.copy()
    environment.setdefault("CONTINUCARE_DB_PATH", str(settings.db_path))
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for module in ("continucare.patient_web", "continucare.doctor_web"):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", module],
                    cwd=PROJECT_ROOT,
                    env=environment,
                )
            )
        _wait_until_ready(processes)
        print("三角色网页已就绪。按 Ctrl+C 同时停止两个服务。")
        if args.open:
            for url in (DOCTOR_URL, PATIENT_URL, NURSE_URL):
                webbrowser.open_new_tab(url)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        failed = next(process for process in processes if process.poll() is not None)
        raise RuntimeError(f"网页服务意外退出（exit {failed.returncode}）")
    except KeyboardInterrupt:
        print("\n正在停止 ContinuCare 网页服务……")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"无法启动网页服务：{exc}", file=sys.stderr)
        return 1
    finally:
        _stop(processes)


if __name__ == "__main__":
    raise SystemExit(main())
