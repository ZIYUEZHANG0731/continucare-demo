from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated(
    script_name: str, *script_args: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT_ROOT / "scripts" / script_name),
            *script_args,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_semantic_evaluation_script_runs_without_editable_import_path():
    result = _run_isolated("evaluate_semantic_layer.py")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["details"]
    assert all(item["passed"] for item in report["details"])


def test_semantic_evaluation_prefers_current_checkout_over_shadow_package(tmp_path):
    shadow_package = tmp_path / "shadow" / "continucare"
    shadow_package.mkdir(parents=True)
    (shadow_package / "__init__.py").write_text(
        'raise RuntimeError("shadow checkout imported")\n',
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    runner = (
        "import runpy, sys; "
        "sys.path.insert(0, sys.argv[1]); "
        "script = sys.argv[2]; "
        "sys.argv = [script]; "
        "runpy.run_path(script, run_name='__main__')"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            runner,
            str(shadow_package.parent),
            str(PROJECT_ROOT / "scripts" / "evaluate_semantic_layer.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["release"]["release_id"] == "continucare-layer3-v1.1.0"


def test_demo_rehearsal_script_runs_without_editable_import_path():
    result = _run_isolated("rehearse_demo.py")

    assert result.returncode == 0, result.stderr
    assert "rehearsal 3/3: passed" in result.stdout


def test_fhir_validation_script_runs_without_editable_import_path():
    result = _run_isolated("validate_fhir_r4.py", "--help")

    assert result.returncode == 0, result.stderr
    assert "--schema" in result.stdout


@pytest.mark.parametrize(
    "script_name",
    [
        "build_cn_glp1_knowledge.py",
        "check_cn_glp1_sources.py",
        "evaluate_mimo_live.py",
        "evaluate_summary_live.py",
        "mimo_smoke_test.py",
        "validate_cn_glp1_knowledge.py",
    ],
)
def test_remaining_entrypoints_resolve_the_current_checkout(script_name):
    result = _run_isolated(script_name, "--help")

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
