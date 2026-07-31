"""Deterministic local extraction for the fixed synthetic demo scenarios.

This is intentionally a rules-and-templates Mock. It does not call or imitate a
remote language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Pattern
from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.fhir.observations import (
    build_patient_reported_observation,
    millilitres_per_24_hours,
    per_day_quantity,
)
from continucare.fhir.terminology import (
    ABDOMINAL_PAIN_FINDING,
    FLUID_INTAKE_24H_ESTIMATED,
    NAUSEA_FINDING,
    VOMITING_COUNT_24H,
    CodingDefinition,
)
from continucare.models import (
    ConfidenceTier,
    ExtractionResult,
    FollowUpMessage,
    Observation,
    SummaryContent,
    SummaryContext,
    SummaryDraft,
    SummaryItem,
)


@dataclass(frozen=True)
class PhraseRule:
    code: CodingDefinition
    pattern: Pattern[str]
    value: Any = True
    value_element: str = "valueBoolean"
    exclude_context: bool = True


_NUMBER_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


PHRASE_RULES = (
    PhraseRule(NAUSEA_FINDING, re.compile(r"恶心")),
    PhraseRule(ABDOMINAL_PAIN_FINDING, re.compile(r"腹痛|肚子痛|肚子疼")),
)


class MockExtractor:
    """Stable local rules/templates extractor for synthetic demo content."""

    mode = "local_mock_rules"

    def extract(self, message: FollowUpMessage) -> ExtractionResult:
        text = message.message_text
        observations: list[Observation] = []

        vomiting_match = re.search(r"吐了?([一二两三四五六七八九十两\d]+)次", text)
        if vomiting_match and not _is_negated_or_historical(
            text, vomiting_match.start(), vomiting_match.end()
        ):
            count_text = vomiting_match.group(1)
            count = int(count_text) if count_text.isdigit() else _NUMBER_MAP.get(count_text)
            if count is not None:
                observations.append(
                    self._observation(
                        message,
                        code=VOMITING_COUNT_24H,
                        value_element="valueQuantity",
                        value=per_day_quantity(count, unit="vomiting episodes/24 hours"),
                        match=vomiting_match,
                        effective_period_hours=24,
                    )
                )

        fluid_match = re.search(
            r"(?:喝水|饮水|液体摄入).{0,6}?([0-9]+(?:\.[0-9]+)?)\s*(毫升|ml|mL|升|L)",
            text,
        )
        if fluid_match and not _is_negated_or_historical(
            text, fluid_match.start(), fluid_match.end()
        ):
            amount = float(fluid_match.group(1))
            if fluid_match.group(2) in {"升", "L"}:
                amount *= 1000
            observations.append(
                self._observation(
                    message,
                    code=FLUID_INTAKE_24H_ESTIMATED,
                    value_element="valueQuantity",
                    value=millilitres_per_24_hours(amount),
                    match=fluid_match,
                    effective_period_hours=24,
                )
            )

        for rule in PHRASE_RULES:
            match = rule.pattern.search(text)
            if not match:
                continue
            if rule.exclude_context and _is_negated_or_historical(
                text, match.start(), match.end()
            ):
                continue
            observations.append(
                self._observation(
                    message,
                    code=rule.code,
                    value_element=rule.value_element,
                    value=rule.value,
                    match=match,
                )
            )

        observations.sort(key=lambda item: item.evidence_start)
        return ExtractionResult(observations=observations, extractor_mode=self.mode)

    def generate_summary(self, context: SummaryContext) -> SummaryDraft:
        content = SummaryContent()
        observation_refs = [item.observation_id for item in context.observations]
        message_refs = [item.message_id for item in context.messages]
        if message_refs or observation_refs:
            content.overview.append(
                SummaryItem(
                    text=(
                        f"本期收到 {len(context.messages)} 次合成随访，"
                        f"形成 {len(context.observations)} 条患者报告 Observation。"
                    ),
                    evidence_refs=message_refs + observation_refs,
                )
            )

        for observation in context.observations:
            refs = [observation.message_id, observation.observation_id]
            if observation.confidence_tier.value == "model_inferred":
                content.doctor_to_confirm.append(
                    SummaryItem(
                        text=f"抽取字段 {observation.code} 为推断内容，需要医生确认。",
                        evidence_refs=refs,
                    )
                )
                continue
            text = _observation_summary_text(observation)
            if text:
                content.key_changes.append(
                    SummaryItem(text=text, evidence_refs=refs)
                )

        actions_by_alert: dict[str, list] = {}
        for action in context.alert_actions:
            actions_by_alert.setdefault(action.alert_id, []).append(action)
        for alert in context.alerts:
            actions = actions_by_alert.get(alert.alert_id, [])
            action_refs = [action.action_id for action in actions]
            if alert.resolution_reason:
                outcome = f"最终处理结果：{alert.resolution_reason}"
            elif actions:
                outcome = f"当前状态 {alert.status.value}；最近记录：{actions[-1].note}"
            else:
                outcome = f"当前状态 {alert.status.value}，尚无处理记录"
            content.alerts_and_actions.append(
                SummaryItem(
                    text=(
                        f"{alert.severity} Alert 由 {alert.trigger_rule_id} 触发；"
                        f"触发原因：{alert.trigger_reason}；{outcome}。"
                    ),
                    evidence_refs=[alert.alert_id] + alert.evidence_refs + action_refs,
                )
            )
            if alert.status.value != "resolved":
                content.doctor_to_confirm.append(
                    SummaryItem(
                        text=f"请确认尚未关闭的 {alert.severity} 工作流 Alert。",
                        evidence_refs=[alert.alert_id],
                    )
                )

        for message in context.messages:
            if re.search(r"(?:药|剂量|用药).{0,8}(?:调整|改|停|怎么办|要不要)", message.message_text):
                content.patient_questions.append(
                    SummaryItem(
                        text="患者希望医生确认是否需要调整。",
                        evidence_refs=[message.message_id],
                    )
                )

        return SummaryDraft(content=content)

    @staticmethod
    def _observation(
        message: FollowUpMessage,
        *,
        code: CodingDefinition,
        value_element: str,
        value: Any,
        match: re.Match[str],
        effective_period_hours: int | None = None,
    ) -> Observation:
        now = utc_now_iso()
        resource = build_patient_reported_observation(
            observation_id=f"observation-{uuid4().hex}",
            patient_id=message.patient_id,
            questionnaire_response_id=message.message_id,
            effective_time=message.submitted_at,
            code=code,
            value_element=value_element,
            value=value,
            effective_period_hours=effective_period_hours,
        )
        return Observation(
            resource=resource,
            evidence={
                "questionnaire_response_id": message.message_id,
                "confidence_tier": ConfidenceTier.VERBATIM_EXPLICIT,
                "evidence_text": match.group(0),
                "evidence_start": match.start(),
                "evidence_end": match.end(),
                "recorded_at": now,
            },
        )


def _is_negated_or_historical(text: str, start: int, end: int) -> bool:
    """Exclude fixed negated and historical contexts without clinical inference."""

    prefix = text[max(0, start - 12) : start]
    suffix = text[end : min(len(text), end + 12)]

    if re.search(r"(?:没有|没|无|否认|不)(?:再|觉得|感到|出现)?$", prefix):
        return True
    # Do not treat an explicit measurement window such as "过去24小时" as
    # historical context; it is the required time basis for the LOINC metrics.
    if re.search(r"(?:上个月|以前|之前|曾经|既往).{0,6}$", prefix):
        return True
    if re.search(r"过.{0,6}(?:现在|目前)(?:没有|没|无)", suffix):
        return True
    return False


def _observation_summary_text(observation: Observation) -> str | None:
    if observation.code == "94070-0":
        return f"患者原文报告呕吐 {observation.value} 次。"
    if observation.code == "75301-2":
        return f"患者原文报告过去24小时估计液体摄入 {observation.value_display}。"
    if observation.code == "422587007":
        return "患者原文报告恶心。"
    if observation.code == "21522001":
        return "患者原文报告腹痛。"
    return f"患者报告 {observation.code_display} = {observation.value_display}。"
