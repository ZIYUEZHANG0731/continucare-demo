"""Single-run synthetic evaluation against the configured live Xiaomi MiMo API."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import CandidateIssueAction
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, build_model_adapter
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID


DEFAULT_CASES = (
    Path(__file__).parents[1] / "tests" / "fixtures" / "mimo_live_cases_v1.json"
)
DEFAULT_OUTPUT = Path("/tmp/continucare-mimo-live-evaluation.json")


def _candidate_answers(result) -> dict[str, Any]:
    return {
        candidate.link_id: candidate.answer
        for candidate in result.candidates
    }


def _clarification_links(result) -> list[str]:
    return sorted(
        clarification.proposed_candidate.link_id
        for clarification in result.clarifications
        if clarification.proposed_candidate is not None
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the configured live MiMo model with synthetic text only. "
            "This is a single-run engineering evaluation, not clinical validation."
        )
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fail-on-mismatch", action="store_true")
    args = parser.parse_args()

    config = SemanticModelConfig.from_environment()
    adapter = build_model_adapter(config)
    if not adapter.configured:
        raise SystemExit(
            "MiMo is not configured. Put a rotated MIMO_API_KEY in the ignored .env file."
        )

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    details: list[dict[str, Any]] = []
    total_tokens = 0
    total_latency_ms = 0

    with tempfile.TemporaryDirectory(prefix="continucare-mimo-live-eval-") as directory:
        for index, case in enumerate(cases):
            store = SQLiteStore(Path(directory) / f"case-{index}.db")
            engine = CareEngine(store)
            session = engine.start_or_resume(DEMO_PATIENT_ID)
            started = time.perf_counter()
            interaction = CareAgentService(
                store,
                care_engine=engine,
                model_adapter=adapter,
            ).analyze(session.session_id, case["text"])
            latency_ms = round((time.perf_counter() - started) * 1000)
            result = interaction.result
            actual_answers = _candidate_answers(result)
            actual_clarifications = _clarification_links(result)
            rejected_issues = [
                issue
                for issue in result.candidate_issues
                if issue.action == CandidateIssueAction.REJECTED
            ]
            checks = {
                "live_provider_mode": result.mode == "model_api:xiaomi_mimo",
                "status_exact": result.status.value == case["expected_status"],
                "candidate_links_exact": sorted(actual_answers)
                == sorted(case["expected_candidates"]),
                "candidate_answers_exact": actual_answers
                == case["expected_candidates"],
                "clarification_links_exact": actual_clarifications
                == sorted(case["expected_clarification_links"]),
            }
            usage = result.model_usage or {}
            case_tokens = usage.get("total_tokens", 0)
            total_tokens += case_tokens
            total_latency_ms += latency_ms
            details.append(
                {
                    "case_id": case["case_id"],
                    "passed": all(checks.values()),
                    "clean_model_output": not rejected_issues
                    and not result.safety_violations,
                    "latency_ms": latency_ms,
                    "tokens": case_tokens,
                    "checks": checks,
                    "expected": {
                        "status": case["expected_status"],
                        "candidates": case["expected_candidates"],
                        "clarification_links": case[
                            "expected_clarification_links"
                        ],
                    },
                    "actual": {
                        "mode": result.mode,
                        "status": result.status.value,
                        "candidates": actual_answers,
                        "clarification_links": actual_clarifications,
                        "candidate_issues": [
                            {
                                "link_id": issue.link_id,
                                "action": issue.action.value,
                                "reason_codes": issue.reason_codes,
                            }
                            for issue in result.candidate_issues
                        ],
                        "safety_violations": result.safety_violations,
                    },
                }
            )

    passed = sum(item["passed"] for item in details)
    clean = sum(item["clean_model_output"] for item in details)
    output = {
        "evaluation_scope": (
            "single_run_synthetic_engineering_evaluation_not_clinical_validation"
        ),
        "provider": config.provider,
        "model": config.model_name,
        "case_set": args.cases.name,
        "totals": {
            "cases": len(details),
            "end_to_end_exact": passed,
            "clean_model_output": clean,
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency_ms,
            "average_latency_ms": round(total_latency_ms / len(details)),
        },
        "all_end_to_end_exact": passed == len(details),
        "details": details,
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"report: {args.output}")
    if args.fail_on_mismatch and not output["all_end_to_end_exact"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
