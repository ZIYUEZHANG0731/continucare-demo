from __future__ import annotations

from types import SimpleNamespace

import pytest

from continucare.doctor_planning import _period


@pytest.mark.parametrize(
    ("next_visit_date", "expected_end"),
    [
        ("2026-08-16", "2026-08-16"),
        ("2026-08-15", "2026-08-30"),
    ],
)
def test_plan_period_uses_patient_local_date_at_timezone_boundary(
    monkeypatch, next_visit_date, expected_end
):
    # Shanghai is already on 08-16 while UTC and Berlin are still on 08-15.
    monkeypatch.setenv(
        "CONTINUCARE_SYNTHETIC_NOW", "2026-08-16T00:30:00+08:00"
    )

    assert _period(SimpleNamespace(next_visit_date=next_visit_date)) == (
        "2026-08-16",
        expected_end,
    )
