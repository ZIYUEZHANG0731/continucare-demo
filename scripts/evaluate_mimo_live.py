"""Single-run synthetic evaluation against the configured live Xiaomi MiMo API."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import CandidateIssueAction
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, build_model_adapter
from continucare.care_agent.release import LAYER3_RELEASE
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
    if not config.safety_llm_enabled or not config.language_llm_enabled:
        raise SystemExit(
            "Enable CONTINUCARE_USE_SAFETY_LLM and "
            "CONTINUCARE_USE_LANGUAGE_LLM for the full Layer-3 evaluation."
        )
    release_config = {
        "model": config.model_name,
        "extraction": config.prompt_version,
        "safety": config.safety_prompt_version,
        "language": config.language_prompt_version,
    }
    expected_release_config = {
        "model": LAYER3_RELEASE.model_name,
        "extraction": LAYER3_RELEASE.extraction_prompt_version,
        "safety": LAYER3_RELEASE.safety_prompt_version,
        "language": LAYER3_RELEASE.language_prompt_version,
    }
    if release_config != expected_release_config:
        raise SystemExit(
            "Configured model/Prompt versions do not match the frozen Layer-3 "
            f"release: expected {expected_release_config}, got {release_config}"
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
            stages = {trace.stage: trace for trace in result.stage_traces}
            safety_trace = stages.get("safety_critic")
            language_trace = stages.get("language_rewrite")
            language_applicable = bool(
                result.candidates or result.clarifications
            )
            checks = {
                "live_provider_mode": result.mode == "model_api:xiaomi_mimo",
                "safety_critic_live": bool(
                    safety_trace
                    and safety_trace.mode == "model_api:xiaomi_mimo"
                ),
                "language_rewriter_live_or_not_applicable": bool(
                    language_trace
                    and (
                        language_trace.mode == "model_api:xiaomi_mimo"
                        if language_applicable
                        else language_trace.mode == "not_applicable"
                    )
                ),
                "status_exact": result.status.value == case["expected_status"],
                "candidate_links_exact": sorted(actual_answers)
                == sorted(case["expected_candidates"]),
                "candidate_answers_exact": actual_answers
                == case["expected_candidates"],
                "clarification_links_exact": actual_clarifications
                == sorted(case["expected_clarification_links"]),
            }
            business_result_exact = all(
                checks[key]
                for key in (
                    "status_exact",
                    "candidate_links_exact",
                    "candidate_answers_exact",
                    "clarification_links_exact",
                )
            )
            usage = result.model_usage or {}
            case_tokens = usage.get("total_tokens", 0)
            total_tokens += case_tokens
            total_latency_ms += latency_ms
            details.append(
                {
                    "case_id": case["case_id"],
                    "passed": all(checks.values()),
                    "business_result_exact": business_result_exact,
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
                        "patient_messages": {
                            candidate.link_id: candidate.patient_message
                            for candidate in result.candidates
                        },
                        "clarification_links": actual_clarifications,
                        "clarification_prompts": [
                            item.prompt for item in result.clarifications
                        ],
                        "candidate_issues": [
                            {
                                "link_id": issue.link_id,
                                "action": issue.action.value,
                                "reason_codes": issue.reason_codes,
                            }
                            for issue in result.candidate_issues
                        ],
                        "safety_violations": result.safety_violations,
                        "stage_traces": [
                            trace.model_dump(mode="json")
                            for trace in result.stage_traces
                        ],
                    },
                }
            )

    passed = sum(item["passed"] for item in details)
    business_passed = sum(item["business_result_exact"] for item in details)
    clean = sum(item["clean_model_output"] for item in details)
    output = {
        "release": LAYER3_RELEASE.as_dict(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": (
            "single_run_synthetic_engineering_evaluation_not_clinical_validation"
        ),
        "provider": config.provider,
        "model": config.model_name,
        "prompt_versions": {
            "extraction": config.prompt_version,
            "safety": config.safety_prompt_version,
            "language": config.language_prompt_version,
        },
        "case_set": args.cases.name,
        "totals": {
            "cases": len(details),
            "business_result_exact": business_passed,
            "full_pipeline_exact": passed,
            "clean_model_output": clean,
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency_ms,
            "average_latency_ms": round(total_latency_ms / len(details)),
        },
        "all_business_results_exact": business_passed == len(details),
        "all_full_pipeline_exact": passed == len(details),
        "details": details,
    }
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"report: {args.output}")
    if args.fail_on_mismatch and not output["all_full_pipeline_exact"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
