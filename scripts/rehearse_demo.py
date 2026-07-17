"""Run the M5 synthetic story three times without external services."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.services.alerts import AlertService
from continucare.services.demo_scenarios import load_scenario
from continucare.services.summaries import SummaryService


def rehearse_once(db_path: Path) -> None:
    normal = load_scenario(db_path, "正常路径")
    assert normal.decision.severity == "L0" and normal.alert is None

    l2 = load_scenario(db_path, "L2 工作流")
    assert l2.decision.severity == "L2" and l2.alert is not None
    store = SQLiteStore(db_path)
    alerts = AlertService(store, MockNotifier())
    alerts.acknowledge(l2.alert.alert_id)
    alerts.resolve(l2.alert.alert_id, "已完成合成演示复核并留痕")
    summaries = SummaryService(store, MockExtractor(), MockNotifier())
    summary = summaries.generate(DEMO_PATIENT_ID)
    summaries.review(summary.summary_id)
    assert SQLiteStore(db_path).get_summary(summary.summary_id).status == "reviewed"

    l4 = load_scenario(db_path, "L4 红旗")
    assert l4.decision.severity == "L4" and l4.alert is not None


def main() -> None:
    with TemporaryDirectory(prefix="continucare-rehearsal-") as directory:
        for run in range(1, 4):
            rehearse_once(Path(directory) / f"run-{run}.db")
            print(f"rehearsal {run}/3: passed")


if __name__ == "__main__":
    main()

