from __future__ import annotations

import pytest

from scripts.rehearse_demo import rehearse_once


@pytest.mark.parametrize("run_number", [1, 2, 3])
def test_three_consecutive_demo_rehearsals(tmp_path, run_number):
    rehearse_once(tmp_path / f"rehearsal-{run_number}.db")


def test_one_click_scenario_reset_removes_previous_story(tmp_path):
    from continucare.adapters.sqlite_store import SQLiteStore
    from continucare.services.demo_scenarios import load_scenario

    db_path = tmp_path / "scenarios.db"
    l2 = load_scenario(db_path, "L2 工作流")
    assert l2.alert is not None

    normal = load_scenario(db_path, "正常路径")
    store = SQLiteStore(db_path)

    assert normal.alert is None
    assert store.list_alerts() == []
    assert len(store.list_messages("P-DEMO-001")) == 1

