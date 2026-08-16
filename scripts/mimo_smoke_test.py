"""One synthetic model call using the ignored local environment."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.errors import ModelRequestError, ModelResponseError
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import (
    MODEL_API_MODES,
    SemanticModelConfig,
    build_model_adapter,
)
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one synthetic-data semantic extraction model call."
    )
    parser.add_argument(
        "--text",
        default="过去24小时我呕吐了2次，现在有点恶心。",
        help="Synthetic patient wording only; never pass real patient data.",
    )
    args = parser.parse_args()
    config = SemanticModelConfig.from_environment()
    adapter = build_model_adapter(config)
    if not adapter.configured:
        raise SystemExit(
            "The model is not configured. Put its API key in the ignored .env file."
        )
    diagnostic_errors: list[str] = []
    original_extract = adapter.extract

    def extract_with_sanitized_diagnostics(task):
        try:
            return original_extract(task)
        except (ModelRequestError, ModelResponseError) as exc:
            diagnostic_errors.append(f"{type(exc).__name__}: {exc}")
            raise

    adapter.extract = extract_with_sanitized_diagnostics
    with tempfile.TemporaryDirectory(prefix="continucare-mimo-smoke-") as directory:
        store = SQLiteStore(Path(directory) / "smoke.db")
        engine = CareEngine(store)
        session = engine.start_or_resume(DEMO_PATIENT_ID)
        interaction = CareAgentService(
            store, care_engine=engine, model_adapter=adapter
        ).analyze(
            session.session_id,
            args.text,
        )
        if interaction.result.mode not in MODEL_API_MODES:
            sanitized_reasons = [
                reason
                for reason in interaction.result.ignored_reasons
                if reason.startswith("model_adapter_error_fallback:")
            ]
            detail = sanitized_reasons[0] if sanitized_reasons else "unknown_error"
            if diagnostic_errors:
                detail = diagnostic_errors[0]
            raise SystemExit(
                "Model call did not pass the adapter contract: " + detail
            )
        print(
            json.dumps(
                {
                    "provider": interaction.record.model_provider,
                    "model": interaction.record.model_name,
                    "status": interaction.result.status.value,
                    "candidate_link_ids": [
                        item.link_id for item in interaction.result.candidates
                    ],
                    "candidates": [
                        {
                            "link_id": item.link_id,
                            "answer": item.answer,
                            "evidence_text": item.evidence_text,
                            "patient_message": item.patient_message,
                        }
                        for item in interaction.result.candidates
                    ],
                    "clarification_count": len(
                        interaction.result.clarifications
                    ),
                    "clarifications": [
                        {
                            "kind": item.kind.value,
                            "prompt": item.prompt,
                        }
                        for item in interaction.result.clarifications
                    ],
                    "safety_violation_count": len(
                        interaction.result.safety_violations
                    ),
                    "candidate_issues": [
                        {
                            "link_id": issue.link_id,
                            "action": issue.action.value,
                            "reason_codes": issue.reason_codes,
                        }
                        for issue in interaction.result.candidate_issues
                    ],
                    "usage": interaction.result.model_usage,
                    "request_id": interaction.result.provider_request_id,
                    "stage_traces": [
                        {
                            "stage": trace.stage,
                            "mode": trace.mode,
                            "status": trace.status,
                            "prompt_version": trace.prompt_version,
                            "usage": trace.model_usage,
                            "latency_ms": trace.latency_ms,
                            "details": trace.details,
                        }
                        for trace in interaction.result.stage_traces
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
