"""Deterministic pure risk-priority rules.

The functions in this module do not perform diagnosis and do not call an LLM.
They translate already extracted, current patient-reported facts into workflow
priority only.
"""

from __future__ import annotations

from continucare.models import Observation, RiskDecision


EMERGENCY_NOTICE = (
    "系统不是急救通道。你的描述包含需要尽快获得医疗帮助的信号，"
    "请立即联系当地急救或前往急诊，同时系统会通知医护团队。"
)


def evaluate_risk(observations: list[Observation]) -> RiskDecision:
    emergency = [
        item
        for item in observations
        if item.code.startswith("emergency_") and item.value is True
    ]
    if emergency:
        return RiskDecision(
            severity="L4",
            create_alert=True,
            title="当前描述包含急症红旗表达",
            trigger_rule_id="EMERGENCY-001",
            trigger_reason="当前表达命中未被否定或既往描述排除的固定红旗词组",
            evidence_refs=_evidence_refs(emergency),
            owner_role="on_call_clinician",
            sla_hours=0,
        )

    vomiting = next(
        (
            item
            for item in observations
            if item.code == "vomiting_count"
            and isinstance(item.value, (int, float))
            and not isinstance(item.value, bool)
            and item.value >= 1
        ),
        None,
    )
    reduced_fluid = next(
        (
            item
            for item in observations
            if item.code == "fluid_intake_reduced" and item.value is True
        ),
        None,
    )
    if vomiting and reduced_fluid:
        return RiskDecision(
            severity="L2",
            create_alert=True,
            title="需要护士在 24 小时内复核的随访组合",
            trigger_rule_id="GLP1-002",
            trigger_reason="vomiting_count >= 1 且 fluid_intake_reduced = true",
            evidence_refs=_evidence_refs([vomiting, reduced_fluid]),
            owner_role="nurse",
            sla_hours=24,
        )

    return RiskDecision(severity="L0", create_alert=False)


def _evidence_refs(observations: list[Observation]) -> list[str]:
    refs: list[str] = []
    for item in observations:
        if item.message_id not in refs:
            refs.append(item.message_id)
        refs.append(item.observation_id)
    return refs

