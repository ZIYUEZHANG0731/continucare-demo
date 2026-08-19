"""Evidence-linked, doctor-confirmed follow-up planning for the standalone portal."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.db import connect, initialize_database, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.doctor_portal import (
    DoctorPortalBoundaryError,
    _allowed_patient_ids,
    canonical_db_path,
)
from continucare.knowledge import load_cn_glp1_release
from continucare.pathways import load_builtin_pathways
from continucare.record_points import (
    project_record_points,
    record_point_metadata,
    validate_record_point_frequencies,
    validate_record_point_selection,
)
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    read_competition_demo,
    persist_doctor_plan_with_activation,
)
from continucare.services.plan_collection import METRIC_TO_LINK_ID


FREQUENCIES = {
    "daily": "每天",
    "every_other_day": "隔天",
    "twice_weekly": "每周2次",
    "weekly": "每周1次",
    "symptom_triggered": "出现时记录",
}
MAX_PLAN_DAYS = 90
MAX_CUSTOM_METRICS = 12
CUSTOM_DATA_TYPES = {
    "quantity": "数值",
    "boolean": "是/否",
    "coded_scale": "分级",
    "text": "文字",
}


def _load_goal_rules() -> dict[str, Any]:
    resource = files("continucare.doctor_data").joinpath(
        "followup_goal_rules_v1.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1 or not payload.get("ruleSetId"):
        raise DoctorPortalBoundaryError("随访目标规则版本无效")
    return payload


def _matches_goal_rule(rule: dict[str, Any], profile: dict[str, Any], pathway) -> bool:
    match = rule.get("match", {})
    predicates = (
        ("indications", profile.get("indication")),
        ("populations", profile.get("population")),
        ("products", profile.get("productId")),
        ("pathwayCodes", pathway.code),
    )
    return all(not match.get(field) or value in match[field] for field, value in predicates)


def _goal_candidates(
    rules: dict[str, Any], profile: dict[str, Any], pathway, context_by_id
) -> list[dict[str, Any]]:
    source_by_id = {item["sourceId"]: item for item in rules.get("sources", [])}
    candidates: list[dict[str, Any]] = []
    for rule in rules.get("rules", []):
        if not _matches_goal_rule(rule, profile, pathway):
            continue
        for metric in rule.get("metrics", []):
            context_refs = [
                item for item in metric.get("contextRefs", []) if item in context_by_id
            ]
            evidence = []
            for item in metric.get("evidence", []):
                source = source_by_id.get(item.get("sourceId"), {})
                evidence.append(
                    {
                        **item,
                        "sourceTitle": source.get("title", item.get("sourceId")),
                        "authority": source.get("authority", "来源待解析"),
                        "canonicalUrl": source.get("canonicalUrl"),
                        "runtimeEligible": False,
                    }
                )
            candidates.append(
                {
                    **metric,
                    "selectedByDefault": True,
                    "required": metric.get("selectionPolicy") == "required",
                    "sourceType": "treatment_goal_rule",
                    "ruleId": rule["ruleId"],
                    "frequencyOptions": [
                        {"value": value, "label": label}
                        for value, label in FREQUENCIES.items()
                    ],
                    "contextUsed": [context_by_id[item] for item in context_refs],
                    "evidence": evidence,
                    "approvalStatus": rules.get("status"),
                    "clinicalInterpretationAllowed": False,
                }
            )
    return candidates


def _load_synthetic_ehr_profile(patient_id: str) -> dict[str, Any] | None:
    resource = files("continucare.doctor_data").joinpath(
        "synthetic_ehr_profiles_v1.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    for profile in payload.get("profiles", []):
        if profile.get("patientId") == patient_id:
            return dict(profile)
    return None


def _ensure_plan_schema(db_path: Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctor_followup_plans (
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL CHECK (plan_version > 0),
                patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
                pathway_code TEXT NOT NULL,
                pathway_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('confirmed', 'superseded')),
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                knowledge_release_id TEXT NOT NULL,
                ehr_snapshot_id TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                PRIMARY KEY (plan_id, plan_version)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_doctor_plan_current
            ON doctor_followup_plans(patient_id, pathway_code) WHERE is_current = 1;
            CREATE INDEX IF NOT EXISTS idx_doctor_plan_patient_time
            ON doctor_followup_plans(patient_id, created_at);
            """
        )


def _authorized_patient(patient_id: str):
    if patient_id not in _allowed_patient_ids():
        raise DoctorPortalBoundaryError("该患者不在当前医生的授权范围内")
    db_path = canonical_db_path()
    initialize_database(db_path)
    store = SQLiteStore(db_path, initialize=False)
    patient = store.get_patient(patient_id)
    if patient is None:
        raise DoctorPortalBoundaryError("患者记录不存在")
    return db_path, patient


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DoctorPortalBoundaryError(f"{field_name}日期无效") from exc


def _patient_today() -> date:
    """Return the fixed demo patient's local date from the shared clock."""

    instant = datetime.fromisoformat(utc_now_iso())
    if instant.tzinfo is None:
        raise ValueError("utc_now_iso() must return a timezone-aware timestamp")
    # Keep this fixed demo-patient zone aligned with the next-check-in boundary
    # in services.competition_demo.
    return instant.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _period(patient) -> tuple[str, str]:
    today = _patient_today()
    try:
        next_visit = date.fromisoformat(patient.next_visit_date)
    except ValueError:
        next_visit = today + timedelta(days=14)
    if next_visit < today:
        next_visit = today + timedelta(days=14)
    return today.isoformat(), next_visit.isoformat()


def _evidence_payload(metric, claim_by_id, source_by_id) -> list[dict[str, Any]]:
    evidence = []
    for claim_id in metric.evidence_claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None:
            continue
        source = source_by_id.get(claim.source_id)
        evidence.append(
            {
                "claimId": claim.claim_id,
                "claim": claim.normalized_claim,
                "allowedUse": list(claim.allowed_use),
                "sourceId": claim.source_id,
                "sourceTitle": source.title if source else claim.source_id,
                "authority": source.authority if source else "来源待解析",
                "canonicalUrl": source.canonical_url if source else None,
                "locator": " / ".join(
                    item
                    for item in (
                        claim.locator.section,
                        claim.locator.subsection,
                        f"第{claim.locator.page}页" if claim.locator.page else None,
                    )
                    if item
                ),
                "runtimeEligible": claim.runtime_eligible,
            }
        )
    return evidence


def _current_plan(db_path: Path, patient_id: str, pathway_code: str):
    _ensure_plan_schema(db_path)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT plan_json FROM doctor_followup_plans "
            "WHERE patient_id=? AND pathway_code=? AND is_current=1",
            (patient_id, pathway_code),
        ).fetchone()
    if row is None:
        return None
    plan = json.loads(row["plan_json"])
    items = [
        {
            **item,
            "recordPointId": item.get("recordPointId")
            or record_point_metadata(str(item.get("metricId") or ""))[
                "recordPointId"
            ],
            "recordPoint": item.get("recordPoint")
            or record_point_metadata(str(item.get("metricId") or "")),
        }
        for item in plan.get("items", [])
        if isinstance(item, dict)
    ]
    return {
        **plan,
        "items": items,
        "recordPoints": project_record_points(items),
        "recordPointCount": len(project_record_points(items)),
    }


def build_followup_plan_proposal(
    patient_id: str = DEMO_PATIENT_ID,
) -> dict[str, Any]:
    """Build a server-owned proposal from minimal EHR context and governed knowledge."""

    db_path, patient = _authorized_patient(patient_id)
    pathway = load_builtin_pathways().get(patient.pathway_code)
    if pathway is None:
        raise DoctorPortalBoundaryError("患者随访路径不可读取")
    profile = _load_synthetic_ehr_profile(patient_id) if patient.synthetic else None
    if profile is None:
        raise DoctorPortalBoundaryError("患者电子档案尚未完成最小必要信息导入")

    release = load_cn_glp1_release().release
    goal_rules = _load_goal_rules()
    claim_by_id = {item.claim_id: item for item in release.evidence_claims}
    source_by_id = {item.source_id: item for item in release.sources}
    context_by_id = {item["contextId"]: item for item in profile["context"]}
    treatment_context_refs = [
        item["contextId"]
        for item in profile["context"]
        if item.get("category") in {"当前治疗", "诊疗背景"}
    ]
    symptom_metrics = {
        concept
        for item in profile["context"]
        for concept in item.get("conceptIds", [])
    }
    start_date, end_date = _period(patient)
    candidates = _goal_candidates(goal_rules, profile, pathway, context_by_id)
    goal_metric_ids = {item["metricId"] for item in candidates}
    for metric in release.metrics:
        if not metric.runtime_eligible:
            continue
        scope_matches = (
            profile["productId"] in metric.product_scope
            and profile["indication"] in metric.indication_scope
            and profile["population"] in metric.population_scope
        )
        if not scope_matches:
            continue
        context_refs = list(treatment_context_refs)
        priority = "重点建议" if metric.metric_id in symptom_metrics else "建议监测"
        category_id = (
            "personalized"
            if metric.metric_id in symptom_metrics
            else "medication_safety"
        )
        if metric.metric_id in symptom_metrics:
            context_refs.extend(
                item["contextId"]
                for item in profile["context"]
                if metric.metric_id in item.get("conceptIds", [])
            )
            reason = (
                "电子病历记录了与该项目相关的近期情况，同时当前药物、"
                "适应证和人群与受控知识包范围精确匹配。"
            )
        else:
            reason = (
                "当前药物、适应证和人群与受控知识包范围精确匹配，"
                "该项目属于现有患者报告采集路径。"
            )
        candidates.append(
            {
                "metricId": metric.metric_id,
                "displayName": metric.display_zh,
                "clinicalIntent": metric.clinical_intent,
                "dataType": metric.data_type,
                "timeWindow": metric.time_window,
                "priority": priority,
                "categoryId": category_id,
                "reason": reason,
                "selectedByDefault": True,
                "required": False,
                "sourceType": "medication_knowledge",
                "defaultFrequency": "daily",
                "frequencyOptions": [
                    {"value": value, "label": label}
                    for value, label in FREQUENCIES.items()
                ],
                "contextRefs": context_refs,
                "contextUsed": [context_by_id[item] for item in context_refs],
                "evidence": _evidence_payload(
                    metric, claim_by_id, source_by_id
                ),
                "approvalStatus": metric.approval_status,
                "clinicalInterpretationAllowed": metric.clinical_interpretation_allowed,
            }
        )

    if not goal_metric_ids:
        raise DoctorPortalBoundaryError("当前路径缺少经过版本管理的核心指标规则")

    category_counts = {
        category["categoryId"]: len(
            {
                record_point_metadata(item["metricId"])["recordPointId"]
                for item in candidates
                if item.get("categoryId") == category["categoryId"]
            }
        )
        for category in goal_rules["categories"]
    }
    categories = [
        {**category, "candidateCount": category_counts[category["categoryId"]]}
        for category in goal_rules["categories"]
    ]

    current_plan = _current_plan(db_path, patient_id, pathway.code)
    current_plan_version = int((current_plan or {}).get("planVersion") or 0)
    proposal_id = (
        f"{patient.patient_id}:{pathway.code}:{pathway.version}:"
        f"{release.manifest.release_id}:{goal_rules['ruleSetId']}:{profile['snapshotId']}:"
        f"plan-v{current_plan_version}"
    )
    return {
        "proposalVersion": 3,
        "proposalId": proposal_id,
        "generatedAt": utc_now_iso(),
        "patientId": patient.patient_id,
        "pathway": {
            "code": pathway.code,
            "version": pathway.version,
            "name": pathway.name,
            "status": pathway.status.value,
        },
        "ehr": {
            "snapshotId": profile["snapshotId"],
            "sourceSystem": profile["sourceSystem"],
            "recordedAt": profile["recordedAt"],
            "context": profile["context"],
            "missingItems": profile["missingItems"],
            "excludedData": profile["excludedData"],
        },
        "knowledge": {
            "releaseId": release.manifest.release_id,
            "goalRuleSetId": goal_rules["ruleSetId"],
            "goalRuleStatus": goal_rules["status"],
            "status": release.manifest.status,
            "jurisdiction": release.manifest.jurisdiction,
            "syntheticOnly": release.manifest.synthetic_only,
            "productionClinicalRuntimeEligible": (
                release.coverage.production_clinical_runtime_eligible
            ),
            "candidateCount": len(candidates),
        },
        "workflow": {
            "composition": [
                "treatment_goal_core",
                "medication_safety",
                "ehr_personalization",
                "doctor_custom",
            ],
            "categories": categories,
            "customMetric": {
                "allowed": True,
                "maximum": MAX_CUSTOM_METRICS,
                "dataTypes": [
                    {"value": value, "label": label}
                    for value, label in CUSTOM_DATA_TYPES.items()
                ],
                "frequencyOptions": [
                    {"value": value, "label": label}
                    for value, label in FREQUENCIES.items()
                ],
            },
        },
        "period": {"startDate": start_date, "endDate": end_date},
        "candidates": [
            {
                **item,
                "recordPoint": record_point_metadata(item["metricId"]),
            }
            for item in candidates
        ],
        "currentPlan": current_plan,
        "activation": {
            "allowed": bool(patient.synthetic and release.manifest.synthetic_only),
            "mode": "synthetic_demo" if patient.synthetic else "clinical_runtime",
            "buttonLabel": "确认并开启演示随访" if patient.synthetic else "确认并开启随访",
            "scopeLabel": "医生确认的随访记录要点",
        },
    }


def _clean_custom_text(value: Any, field_name: str, maximum: int, *, required: bool) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not cleaned:
        raise DoctorPortalBoundaryError(f"自定义指标{field_name}不能为空")
    if len(cleaned) > maximum:
        raise DoctorPortalBoundaryError(f"自定义指标{field_name}不能超过{maximum}个字符")
    return cleaned


def _normalize_custom_items(
    submitted: list[dict[str, Any]], proposal: dict[str, Any], seen_names: set[str]
) -> list[dict[str, Any]]:
    if len(submitted) > MAX_CUSTOM_METRICS:
        raise DoctorPortalBoundaryError(f"自定义指标最多添加{MAX_CUSTOM_METRICS}项")
    current_custom = {
        item["metricId"]: item
        for item in (proposal.get("currentPlan") or {}).get("items", [])
        if item.get("sourceType") == "doctor_custom"
    }
    normalized = []
    seen_custom_ids: set[str] = set()
    for item in submitted:
        display_name = _clean_custom_text(item.get("displayName"), "名称", 40, required=True)
        name_key = display_name.casefold()
        if name_key in seen_names:
            raise DoctorPortalBoundaryError("监测指标名称不能重复")
        seen_names.add(name_key)
        data_type = str(item.get("dataType") or "")
        if data_type not in CUSTOM_DATA_TYPES:
            raise DoctorPortalBoundaryError("自定义指标的数据类型无效")
        frequency = str(item.get("frequency") or "")
        if frequency not in FREQUENCIES:
            raise DoctorPortalBoundaryError("监测频率无效")
        unit = _clean_custom_text(item.get("unit"), "单位", 16, required=False)
        if data_type != "quantity":
            unit = ""
        existing_id = str(item.get("metricId") or "")
        metric_id = (
            existing_id
            if existing_id in current_custom
            else f"custom_{uuid4().hex[:16]}"
        )
        if metric_id in seen_custom_ids:
            raise DoctorPortalBoundaryError("自定义监测指标不能重复")
        seen_custom_ids.add(metric_id)
        normalized.append(
            {
                "metricId": metric_id,
                "displayName": display_name,
                "clinicalIntent": _clean_custom_text(
                    item.get("clinicalIntent"), "说明", 160, required=False
                )
                or "按医生要求记录该指标。",
                "frequency": frequency,
                "frequencyLabel": FREQUENCIES[frequency],
                "dataType": data_type,
                "dataTypeLabel": CUSTOM_DATA_TYPES[data_type],
                "unit": unit,
                "chartKind": "line" if data_type == "quantity" else "status",
                "categoryId": "doctor_custom",
                "sourceType": "doctor_custom",
                "recordPointId": f"metric:{metric_id}",
                "recordPoint": {
                    "recordPointId": f"metric:{metric_id}",
                    "displayName": display_name,
                    "kind": "custom",
                    "metricIds": [metric_id],
                    "linkIds": [],
                    "fields": [],
                    "patientWebTask": False,
                },
                "collectionChannel": "doctor_defined_source",
                "required": False,
                "reason": "由医生结合本次诊疗需要手动添加。",
                "contextRefs": [],
                "evidenceClaimIds": [],
                "collection": {
                    "inputKind": data_type,
                    "unit": unit or None,
                    "requiresPatientInput": True,
                },
            }
        )
    return normalized


def confirm_followup_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a client selection against a fresh proposal and persist a new version."""

    patient_id = str(payload.get("patientId") or "")
    proposal = build_followup_plan_proposal(patient_id)
    if not proposal["activation"]["allowed"]:
        raise DoctorPortalBoundaryError("当前知识版本尚不能开启真实患者随访")
    if payload.get("proposalId") != proposal["proposalId"]:
        raise DoctorPortalBoundaryError("患者档案或知识版本已更新，请刷新后重新确认")
    expected_plan_version = int(
        (proposal.get("currentPlan") or {}).get("planVersion") or 0
    )
    start = _parse_date(str(payload.get("startDate") or ""), "开始")
    end = _parse_date(str(payload.get("endDate") or ""), "结束")
    if end < start:
        raise DoctorPortalBoundaryError("结束日期不能早于开始日期")
    if (end - start).days + 1 > MAX_PLAN_DAYS:
        raise DoctorPortalBoundaryError(f"单次随访周期不能超过{MAX_PLAN_DAYS}天")

    submitted_items = payload.get("items")
    if not isinstance(submitted_items, list) or not submitted_items:
        raise DoctorPortalBoundaryError("请至少选择一个监测指标")
    candidate_by_id = {item["metricId"]: item for item in proposal["candidates"]}
    normalized_items = []
    submitted_custom = []
    seen = set()
    for item in submitted_items:
        if not isinstance(item, dict):
            raise DoctorPortalBoundaryError("监测指标内容无效")
        metric_id = str(item.get("metricId") or "")
        frequency = str(item.get("frequency") or "")
        if item.get("isCustom") is True:
            submitted_custom.append(item)
            continue
        if metric_id in seen or metric_id not in candidate_by_id:
            raise DoctorPortalBoundaryError("监测指标不在当前推荐范围内")
        if frequency not in FREQUENCIES:
            raise DoctorPortalBoundaryError("监测频率无效")
        seen.add(metric_id)
        candidate = candidate_by_id[metric_id]
        record_point = record_point_metadata(metric_id)
        normalized_items.append(
            {
                "metricId": metric_id,
                "displayName": candidate["displayName"],
                "frequency": frequency,
                "frequencyLabel": FREQUENCIES[frequency],
                "dataType": candidate["dataType"],
                "unit": candidate.get("unit", ""),
                "chartKind": candidate.get("chartKind"),
                "categoryId": candidate["categoryId"],
                "sourceType": candidate["sourceType"],
                "required": candidate["required"],
                "reason": candidate["reason"],
                "contextRefs": candidate["contextRefs"],
                "evidenceClaimIds": [
                    evidence["claimId"] for evidence in candidate["evidence"]
                ],
                "recordPointId": record_point["recordPointId"],
                "recordPoint": record_point,
                "collection": candidate.get("collection"),
                "collectionChannel": (
                    "patient_web"
                    if metric_id in METRIC_TO_LINK_ID
                    else "confirmed_plan_source"
                ),
            }
        )

    required_metric_ids = {
        item["metricId"] for item in proposal["candidates"] if item["required"]
    }
    missing_required = required_metric_ids - seen
    if missing_required:
        names = "、".join(candidate_by_id[item]["displayName"] for item in missing_required)
        raise DoctorPortalBoundaryError(f"当前路径的核心必选指标不能移除：{names}")
    seen_names = {item["displayName"].casefold() for item in normalized_items}
    normalized_items.extend(
        _normalize_custom_items(submitted_custom, proposal, seen_names)
    )
    try:
        validate_record_point_selection(
            item["metricId"]
            for item in normalized_items
            if item.get("sourceType") != "doctor_custom"
        )
        validate_record_point_frequencies(normalized_items)
    except ValueError as exc:
        raise DoctorPortalBoundaryError(str(exc)) from exc
    patient_collection_metric_ids = [
        item["metricId"]
        for item in normalized_items
        if item["metricId"] in METRIC_TO_LINK_ID
    ]
    db_path, patient = _authorized_patient(patient_id)
    expected_generation = read_competition_demo(db_path).generation
    pathway = proposal["pathway"]
    created_at = utc_now_iso()

    def persist(staging: Path, activation) -> dict[str, Any]:
        _ensure_plan_schema(staging)
        with connect(staging) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT plan_id, plan_version FROM doctor_followup_plans "
                "WHERE patient_id=? AND pathway_code=? AND is_current=1",
                (patient_id, pathway["code"]),
            ).fetchone()
            locked_version = int(current["plan_version"]) if current else 0
            if locked_version != expected_plan_version:
                raise CompetitionDemoConflict(
                    "随访方案已由另一个页面更新，请刷新后重新确认"
                )
            version = int(current["plan_version"]) + 1 if current else 1
            plan_id = current["plan_id"] if current else f"plan-{uuid4().hex}"
            if current:
                connection.execute(
                    "UPDATE doctor_followup_plans SET status='superseded', is_current=0 "
                    "WHERE plan_id=? AND plan_version=?",
                    (plan_id, current["plan_version"]),
                )
            result = {
                "planId": plan_id,
                "planVersion": version,
                "status": "confirmed",
                "synthetic": patient.synthetic,
                "patientId": patient_id,
                "pathwayCode": pathway["code"],
                "pathwayVersion": pathway["version"],
                "knowledgeReleaseId": proposal["knowledge"]["releaseId"],
                "goalRuleSetId": proposal["knowledge"]["goalRuleSetId"],
                "workflowVersion": proposal["proposalVersion"],
                "ehrSnapshotId": proposal["ehr"]["snapshotId"],
                "activationSessionId": activation.session_id,
                "patientQuestionMetricIds": patient_collection_metric_ids,
                "recordPoints": project_record_points(normalized_items),
                "recordPointCount": len(project_record_points(normalized_items)),
                "period": {"startDate": start.isoformat(), "endDate": end.isoformat()},
                "items": normalized_items,
                "createdBy": "doctor_portal_user",
                "createdAt": created_at,
            }
            connection.execute(
                "INSERT INTO doctor_followup_plans ("
                "plan_id, plan_version, patient_id, pathway_code, pathway_version, "
                "status, period_start, period_end, knowledge_release_id, ehr_snapshot_id, "
                "plan_json, created_by, created_at, is_current"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    plan_id,
                    version,
                    patient_id,
                    pathway["code"],
                    pathway["version"],
                    "confirmed",
                    start.isoformat(),
                    end.isoformat(),
                    proposal["knowledge"]["releaseId"],
                    proposal["ehr"]["snapshotId"],
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    "doctor_portal_user",
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, patient_id, entity_type, entity_id, "
                "event_type, actor_type, details_json, created_at) "
                "VALUES (?, ?, 'FollowUpPlan', ?, 'doctor_plan_confirmed', 'doctor', ?, ?)",
                (
                    f"audit_{uuid4().hex}",
                    patient_id,
                    plan_id,
                    json.dumps(
                        {
                            "plan_version": version,
                            "pathway_code": pathway["code"],
                            "knowledge_release_id": proposal["knowledge"]["releaseId"],
                            "ehr_snapshot_id": proposal["ehr"]["snapshotId"],
                            "activation_session_id": activation.session_id,
                            "metric_ids": [item["metricId"] for item in normalized_items],
                            "period_start": start.isoformat(),
                            "period_end": end.isoformat(),
                            "synthetic_only": True,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    created_at,
                ),
            )
        return result

    result, _ = persist_doctor_plan_with_activation(
        db_path,
        expected_generation=expected_generation,
        persist_plan=persist,
    )
    return result
