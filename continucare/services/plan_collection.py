"""Read-only bridge from a confirmed doctor plan to patient collection fields."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from continucare.record_points import (
    LINK_ID_ORDER,
    METRIC_TO_LINK_ID,
    project_record_points,
)


# Compatibility only for the legacy synthetic activation helper, which has no
# persisted doctor plan to project. Real doctor-confirmed flows always use the
# plan's patientWebTask fields and therefore include body weight when selected.
LEGACY_UNPLANNED_LINK_IDS = tuple(
    link_id for link_id in LINK_ID_ORDER if link_id != "body-weight"
)


def patient_collection_projection(
    db_path: Path | str,
    *,
    patient_id: str,
    pathway_code: str,
) -> dict[str, Any] | None:
    """Return the current plan's patient-web subset without creating schema."""

    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT plan_json FROM doctor_followup_plans "
                "WHERE patient_id=? AND pathway_code=? AND is_current=1",
                (patient_id, pathway_code),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    if row is None:
        return None
    plan = json.loads(row["plan_json"])
    record_points = project_record_points(
        item for item in plan.get("items", []) if isinstance(item, dict)
    )
    selected_patient_metrics = {
        metric_id
        for record_point in record_points
        if record_point.get("patientWebTask")
        for metric_id in record_point.get("metricIds", [])
    }
    link_ids = tuple(
        link_id
        for metric_id, link_id in METRIC_TO_LINK_ID.items()
        if metric_id in selected_patient_metrics
    )
    patient_metrics = tuple(
        metric_id
        for metric_id in METRIC_TO_LINK_ID
        if metric_id in selected_patient_metrics
    )
    return {
        "planId": plan.get("planId"),
        "planVersion": plan.get("planVersion"),
        "linkIds": link_ids,
        "patientQuestionMetricIds": patient_metrics,
        "recordPoints": [
            item for item in record_points if item.get("patientWebTask")
        ],
        "confirmedRecordPointCount": len(record_points),
    }


def active_patient_link_ids(
    db_path: Path | str,
    *,
    patient_id: str,
    pathway_code: str,
    questionnaire: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    projection = patient_collection_projection(
        db_path,
        patient_id=patient_id,
        pathway_code=pathway_code,
    )
    link_ids = (
        tuple(projection["linkIds"])
        if projection is not None
        else LEGACY_UNPLANNED_LINK_IDS
    )
    if questionnaire is None:
        return link_ids
    governed = {
        str(item.get("linkId") or "")
        for item in questionnaire.get("item", [])
        if isinstance(item, dict)
    }
    return tuple(link_id for link_id in link_ids if link_id in governed)
