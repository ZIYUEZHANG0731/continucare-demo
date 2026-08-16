"""Verify the exact post-check-in supplemental path with one synthetic MiMo call."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

import continucare

if Path(continucare.__file__).resolve().parent.parent != PROJECT_ROOT:
    raise RuntimeError("evaluate_supplemental_mimo_live imported the wrong checkout")

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import (
    SemanticModelConfig,
    UnconfiguredModelAdapter,
    build_model_adapter,
)
from continucare.care_engine import CareEngine
from continucare.config import load_local_environment
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.services.competition_demo import (
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.supplemental_reports import (
    read_supplemental_reports,
    resolve_supplemental_turn,
    submit_supplemental_report_turn,
)


SYNTHETIC_TEXT = "我现在还在拉肚子。"


def _complete_daily_checkin(db_path: Path) -> str:
    progress = start_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
    )
    record = store.get_agent_run(progress.run_id)
    if record is None:
        raise RuntimeError("synthetic daily AgentRun missing")
    candidate_ids = [
        item["candidate_id"] for item in record.output_json["candidates"]
    ]
    ConfirmedReviewService(store, care_agent=agent, care_engine=engine).accept_all(
        record.run_id,
        candidate_ids,
    )
    return record.session_id


def main() -> None:
    load_local_environment(PROJECT_ROOT / ".env")
    config = replace(
        SemanticModelConfig.from_environment(),
        safety_llm_enabled=False,
        language_llm_enabled=False,
        summary_llm_enabled=False,
    )
    adapter = build_model_adapter(config)
    if not isinstance(adapter, MiMoSemanticAdapter) or not adapter.configured:
        raise SystemExit("MiMo is not safely configured for the supplemental smoke test.")

    with tempfile.TemporaryDirectory(
        prefix="continucare-supplemental-mimo-"
    ) as directory:
        db_path = Path(directory) / "supplemental-live.db"
        session_id = _complete_daily_checkin(db_path)
        story_before = read_competition_demo(db_path)
        supplemental_before = read_supplemental_reports(
            db_path, session_id=session_id
        )
        submitted = submit_supplemental_report_turn(
            db_path,
            session_id=session_id,
            expected_story_generation=story_before.generation or "",
            expected_supplemental_generation=supplemental_before.generation,
            message_text=SYNTHETIC_TEXT,
            synthetic_confirmed=True,
            model_adapter=adapter,
        )
        if submitted.pending_run_id is None:
            raise RuntimeError("live MiMo did not create a pending supplemental turn")
        accepted = resolve_supplemental_turn(
            db_path,
            session_id=session_id,
            run_id=submitted.pending_run_id,
            decision="accepted",
            expected_story_generation=story_before.generation or "",
            expected_supplemental_generation=submitted.generation,
        )
        report = accepted.reports[-1]
        store = SQLiteStore(db_path, initialize=False)
        observations = store.list_observations_for_message(
            report.questionnaire_response_id or ""
        )
        story_after = read_competition_demo(db_path)
        run = store.get_agent_run(report.source_run_id)
        if (
            run is None
            or run.model_provider != "xiaomi_mimo"
            or story_after.generation != story_before.generation
            or len(observations) != 1
            or observations[0].resource["code"]["coding"][0]["code"]
            != "62315008"
            or story_after.alert_count != 0
            or story_after.approved_clinical_rule_count != 0
        ):
            raise RuntimeError("supplemental live path did not satisfy its frozen boundary")
        print(
            json.dumps(
                {
                    "provider": run.model_provider,
                    "model": run.model_name,
                    "status": "passed",
                    "questionnaire_response_count": 1,
                    "observation_count": len(observations),
                    "observation_code": observations[0].code,
                    "daily_story_unchanged": True,
                    "alert_count": story_after.alert_count,
                    "approved_clinical_rule_count": (
                        story_after.approved_clinical_rule_count
                    ),
                    "synthetic_only": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
