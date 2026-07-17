"""Human-readable labels for the role-facing demo pages.

Business codes remain unchanged in persistence and audit. This module only
translates them for the presentation layer.
"""

from __future__ import annotations

from continucare.models import Alert, AlertStatus, Observation


OBSERVATION_LABELS = {
    "nausea": "报告恶心",
    "vomiting_count": "报告呕吐次数",
    "fluid_intake_reduced": "报告饮水意愿降低",
    "fluid_intake_normal": "报告可以正常喝水",
    "emergency_chest_pain": "当前原文包含胸痛红旗表达",
    "emergency_breathing_difficulty": "当前原文包含呼吸困难红旗表达",
    "emergency_altered_consciousness": "当前原文包含意识/晕厥红旗表达",
    "emergency_heavy_bleeding": "当前原文包含大量出血红旗表达",
}

OWNER_LABELS = {
    "nurse": "随访护士",
    "doctor": "医生",
    "on_call_clinician": "值班医护角色",
}

STATUS_LABELS = {
    AlertStatus.OPEN: "待处理",
    AlertStatus.ACKNOWLEDGED: "已确认收到",
    AlertStatus.ESCALATED: "已升级医生",
    AlertStatus.RESOLVED: "已完成",
}

EVENT_LABELS = {
    "demo_reset": "Demo 已重置",
    "patient_message_submitted": "患者提交院外状态",
    "extraction_completed": "形成结构化患者报告",
    "risk_evaluated": "完成工作流规则检查",
    "risk_rule_matched": "确定性规则命中",
    "alert_created": "创建医护处理任务",
    "notification_mock_sent": "记录模拟飞书通知",
    "nurse_alert_action": "护士更新处理进展",
    "summary_generated": "生成复诊前简报",
    "summary_notification_mock_sent": "记录模拟医生通知",
    "doctor_reviewed_summary": "医生完成简报审阅",
}

ACTOR_LABELS = {
    "synthetic_patient": "合成患者",
    "local_mock_extractor": "本地 Mock 抽取",
    "deterministic_rule_engine": "确定性规则引擎",
    "mock_notifier": "Mock 通知适配器",
    "nurse_demo_user": "演示护士",
    "local_template_generator": "本地摘要模板",
    "doctor_demo_user": "演示医生",
    "demo_operator": "Demo 操作者",
}


def observation_text(observation: Observation) -> str:
    if observation.code == "vomiting_count":
        return f"报告呕吐 {observation.value} 次"
    return OBSERVATION_LABELS.get(
        observation.code, f"患者报告 {observation.code} = {observation.value}"
    )


def observation_evidence_text(observation: Observation) -> str:
    return f"{observation_text(observation)} · 原文“{observation.evidence_text}”"


def alert_status_text(alert: Alert) -> str:
    return STATUS_LABELS.get(alert.status, alert.status.value)


def owner_text(role: str) -> str:
    return OWNER_LABELS.get(role, role)


def alert_next_step(alert: Alert) -> str:
    if alert.severity == "L4":
        return "系统已向患者显示固定急救提示；值班医护角色需要尽快查看并留痕。"
    return "随访护士需要在 SLA 内查看原文证据并记录处理结果。"


def event_text(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, event_type)


def actor_text(actor_type: str) -> str:
    return ACTOR_LABELS.get(actor_type, actor_type)
