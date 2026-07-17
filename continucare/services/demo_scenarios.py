"""One-click, synthetic-only scenario reset and loading."""

from __future__ import annotations

from pathlib import Path

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.db import reset_demo
from continucare.demo_data import DEMO_PATIENT_ID, SCENARIOS
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.workflow import FollowUpWorkflow, WorkflowResult


def load_scenario(db_path: Path | str, scenario_label: str) -> WorkflowResult:
    if scenario_label not in SCENARIOS:
        raise ValueError(f"未知合成场景：{scenario_label}")
    reset_demo(db_path)
    store = SQLiteStore(db_path)
    workflow = FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )
    return workflow.submit(DEMO_PATIENT_ID, SCENARIOS[scenario_label])

