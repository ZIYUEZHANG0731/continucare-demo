"""Offline contract evaluation for the synthetic Layer-3 semantic baseline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID


CASES = Path(__file__).parents[1] / "tests" / "fixtures" / "semantic_cases_v1.json"


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    totals = {
        "cases": len(cases),
        "status_exact": 0,
        "link_set_exact": 0,
        "clarification_count_exact": 0,
        "zero_safety_violations": 0,
    }
    details = []
    with tempfile.TemporaryDirectory(prefix="continucare-layer3-") as directory:
        for index, case in enumerate(cases):
            store = SQLiteStore(Path(directory) / f"case-{index}.db")
            engine = CareEngine(store)
            session = engine.start_or_resume(DEMO_PATIENT_ID)
            result = CareAgentService(store, care_engine=engine).analyze(
                session.session_id, case["text"]
            ).result
            actual_links = sorted(item.link_id for item in result.candidates)
            checks = {
                "status_exact": result.status.value == case["expected_status"],
                "link_set_exact": actual_links == sorted(case["expected_links"]),
                "clarification_count_exact": (
                    len(result.clarifications) == case["expected_clarifications"]
                ),
                "zero_safety_violations": not result.safety_violations,
            }
            for name, passed in checks.items():
                totals[name] += int(passed)
            details.append(
                {
                    "case_id": case["case_id"],
                    "passed": all(checks.values()),
                    "checks": checks,
                }
            )
    output = {
        "evaluation_scope": "synthetic_contract_regression_not_clinical_performance",
        "policy_version": "semantic_cases_v1",
        "totals": totals,
        "all_passed": all(item["passed"] for item in details),
        "details": details,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output["all_passed"] else 1)


if __name__ == "__main__":
    main()
