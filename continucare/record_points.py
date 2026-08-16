"""Single source of truth for doctor-selected follow-up record points.

Record points are the clinician-facing unit.  Questionnaire link IDs and metric
IDs remain the governed storage units, but conditional detail fields are never
presented as unrelated plan items.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable


RECORD_POINT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "recordPointId": "body-weight",
        "displayName": "体重",
        "kind": "outcome",
        "metricIds": ("body_weight",),
        "linkIds": ("body-weight",),
        "fields": (
            {
                "metricId": "body_weight",
                "linkId": "body-weight",
                "role": "value",
                "label": "体重数值",
                "requiredWhenSelected": True,
            },
        ),
        "patientWebTask": True,
    },
    {
        "recordPointId": "nausea",
        "displayName": "恶心",
        "kind": "symptom",
        "metricIds": ("nausea_present_now", "nausea_severity_current"),
        "linkIds": ("nausea-present", "nausea-severity"),
        "fields": (
            {
                "metricId": "nausea_present_now",
                "linkId": "nausea-present",
                "role": "presence",
                "label": "当前是否恶心",
                "requiredWhenSelected": True,
            },
            {
                "metricId": "nausea_severity_current",
                "linkId": "nausea-severity",
                "role": "severity",
                "label": "有恶心时补充程度",
                "requiredWhenSelected": False,
                "enableWhen": {
                    "linkId": "nausea-present",
                    "operator": "=",
                    "answer": True,
                },
            },
        ),
        "patientWebTask": True,
    },
    {
        "recordPointId": "vomiting",
        "displayName": "呕吐",
        "kind": "symptom",
        "metricIds": ("vomiting_count_24h",),
        "linkIds": ("vomiting-count-24h",),
        "fields": (
            {
                "metricId": "vomiting_count_24h",
                "linkId": "vomiting-count-24h",
                "role": "frequency",
                "label": "补充过去24小时次数",
                "requiredWhenSelected": True,
            },
        ),
        "patientWebTask": True,
    },
    {
        "recordPointId": "fluid-intake",
        "displayName": "液体摄入",
        "kind": "intake",
        "metricIds": ("fluid_intake_24h_estimated",),
        "linkIds": ("fluid-intake-24h-estimated",),
        "fields": (
            {
                "metricId": "fluid_intake_24h_estimated",
                "linkId": "fluid-intake-24h-estimated",
                "role": "quantity",
                "label": "补充过去24小时估算量",
                "requiredWhenSelected": True,
            },
        ),
        "patientWebTask": True,
    },
    {
        "recordPointId": "abdominal-pain",
        "displayName": "腹痛",
        "kind": "symptom",
        "metricIds": ("abdominal_pain_present_now",),
        "linkIds": ("abdominal-pain-present",),
        "fields": (
            {
                "metricId": "abdominal_pain_present_now",
                "linkId": "abdominal-pain-present",
                "role": "presence",
                "label": "当前是否腹痛",
                "requiredWhenSelected": True,
            },
        ),
        "patientWebTask": True,
    },
)

_BY_ID = {item["recordPointId"]: item for item in RECORD_POINT_DEFINITIONS}
_BY_METRIC = {
    metric_id: item
    for item in RECORD_POINT_DEFINITIONS
    for metric_id in item["metricIds"]
}
_BY_LINK = {
    link_id: item
    for item in RECORD_POINT_DEFINITIONS
    for link_id in item["linkIds"]
}

METRIC_TO_LINK_ID = {
    field["metricId"]: field["linkId"]
    for definition in RECORD_POINT_DEFINITIONS
    for field in definition["fields"]
    if field.get("linkId")
}
LINK_ID_ORDER = tuple(METRIC_TO_LINK_ID.values())


def record_point_for_metric(metric_id: str) -> dict[str, Any] | None:
    return _BY_METRIC.get(metric_id)


def record_point_for_link(link_id: str) -> dict[str, Any] | None:
    return _BY_LINK.get(link_id)


def record_point_metadata(metric_id: str) -> dict[str, Any]:
    definition = record_point_for_metric(metric_id)
    if definition is None:
        return {
            "recordPointId": f"metric:{metric_id}",
            "displayName": metric_id,
            "kind": "custom",
            "metricIds": [metric_id],
            "linkIds": [],
            "fields": [],
            "patientWebTask": False,
        }
    return {
        "recordPointId": definition["recordPointId"],
        "displayName": definition["displayName"],
        "kind": definition["kind"],
        "metricIds": list(definition["metricIds"]),
        "linkIds": list(definition["linkIds"]),
        "fields": [dict(field) for field in definition["fields"]],
        "patientWebTask": definition["patientWebTask"],
    }


def validate_record_point_selection(metric_ids: Iterable[str]) -> None:
    """Require all governed fields of one selected record point in the plan."""

    selected = set(metric_ids)
    for definition in RECORD_POINT_DEFINITIONS:
        governed = set(definition["metricIds"])
        if selected & governed and not governed <= selected:
            raise ValueError(
                f"{definition['displayName']}必须作为一个完整记录要点选择"
            )


def validate_record_point_frequencies(items: Iterable[dict[str, Any]]) -> None:
    """Enforce one schedule for every clinician-facing record point."""

    frequencies: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for item in items:
        metric_id = str(item.get("metricId") or "")
        metadata = record_point_metadata(metric_id)
        record_point_id = metadata["recordPointId"]
        labels[record_point_id] = metadata["displayName"]
        frequencies.setdefault(record_point_id, set()).add(
            str(item.get("frequency") or "")
        )
    inconsistent = [
        labels[record_point_id]
        for record_point_id, values in frequencies.items()
        if len(values) > 1
    ]
    if inconsistent:
        raise ValueError(f"同一记录要点只能设置一个频率：{'、'.join(inconsistent)}")


def validate_questionnaire_contract(questionnaire: dict[str, Any]) -> None:
    """Fail closed if the governed Questionnaire drifts from the registry."""

    items = {
        str(item.get("linkId") or ""): item
        for item in questionnaire.get("item", [])
        if isinstance(item, dict)
    }
    for definition in RECORD_POINT_DEFINITIONS:
        for field in definition["fields"]:
            link_id = field.get("linkId")
            if not link_id:
                continue
            item = items.get(link_id)
            if item is None:
                # Questionnaire 1.0.0 is immutable and predates direct body-
                # weight capture.  Existing in-progress occurrences stay on
                # that locked contract; 1.1.0 and later must contain it.
                if (
                    link_id == "body-weight"
                    and str(questionnaire.get("version") or "") == "1.0.0"
                ):
                    continue
                raise ValueError(f"记录要点字段缺少问卷定义：{link_id}")
            expected = field.get("enableWhen")
            actual_rows = item.get("enableWhen", [])
            if expected is None:
                if actual_rows:
                    raise ValueError(f"记录要点条件与问卷不一致：{link_id}")
                continue
            actual = actual_rows[0] if len(actual_rows) == 1 else None
            actual_answer = None
            if actual is not None:
                for key, value in actual.items():
                    if key.startswith("answer"):
                        actual_answer = value
                        break
            if actual is None or {
                "linkId": actual.get("question"),
                "operator": actual.get("operator"),
                "answer": actual_answer,
            } != expected:
                raise ValueError(f"记录要点条件与问卷不一致：{link_id}")


def project_record_points(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group persisted flat metric rows into stable clinician-facing units."""

    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in items:
        metric_id = str(item.get("metricId") or "")
        governed = record_point_for_metric(metric_id)
        stored_metadata = item.get("recordPoint")
        # Governed definitions are projected from the current registry so an
        # old plan snapshot cannot permanently suppress a newly published
        # patient collection link. Custom points retain their stored metadata.
        metadata = (
            record_point_metadata(metric_id)
            if governed is not None
            else (
                dict(stored_metadata)
                if isinstance(stored_metadata, dict)
                and stored_metadata.get("recordPointId")
                else record_point_metadata(metric_id)
            )
        )
        record_point_id = str(
            item.get("recordPointId") or metadata["recordPointId"]
        )
        atomic = governed is not None or bool(stored_metadata)
        row = grouped.setdefault(
            record_point_id,
            {
                **metadata,
                "recordPointId": record_point_id,
                "metricIds": list(metadata.get("metricIds", [])) if atomic else [],
                "linkIds": list(metadata.get("linkIds", [])) if atomic else [],
                "selectedFields": [
                    dict(field)
                    for field in metadata.get("fields", [])
                    if field.get("linkId")
                ]
                if atomic
                else [],
            },
        )
        if metric_id and metric_id not in row["metricIds"]:
            row["metricIds"].append(metric_id)
        link_ids = () if atomic else metadata.get("linkIds", [])
        for link_id in link_ids:
            field = next(
                (
                    candidate
                    for candidate in metadata.get("fields", [])
                    if candidate.get("linkId") == link_id
                    and candidate.get("metricId") == metric_id
                ),
                None,
            )
            if field is not None and link_id not in row["linkIds"]:
                row["linkIds"].append(link_id)
                row["selectedFields"].append(field)
    return list(grouped.values())


def group_answer_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for answer in rows:
        link_id = str(answer.get("linkId") or "")
        definition = record_point_for_link(link_id)
        record_point_id = (
            definition["recordPointId"] if definition else f"link:{link_id}"
        )
        group = grouped.setdefault(
            record_point_id,
            {
                "recordPointId": record_point_id,
                "label": definition["displayName"] if definition else answer.get("label"),
                "kind": definition["kind"] if definition else "other",
                "items": [],
            },
        )
        field = next(
            (
                item
                for item in (definition or {}).get("fields", ())
                if item.get("linkId") == link_id
            ),
            None,
        )
        group["items"].append(
            {
                **answer,
                "fieldRole": (field or {}).get("role", "value"),
                "fieldLabel": (field or {}).get("label", answer.get("label")),
            }
        )
    for group in grouped.values():
        group["summary"] = " · ".join(
            str(item.get("value") or "") for item in group["items"]
        )
    return list(grouped.values())
