"""Deterministic collection policy for one synthetic patient check-in."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from continucare.fhir.questionnaires import visible_questionnaire_items
from continucare.models import CareSession
from continucare.db import utc_now_iso
from continucare.services.audit import build_audit_event


COLLECTION_POLICY_VERSION = "glp1-checkin-1.0.0"
COLLECTION_POLICY_VERSIONS = {
    "1.0.0": COLLECTION_POLICY_VERSION,
    "1.1.0": "glp1-checkin-1.1.0",
}
SUPPORTED_PATHWAYS = {("GLP1-14D", "1.0.0"), ("GLP1-14D", "1.1.0")}
SUPPORTED_QUESTIONNAIRE_VERSIONS = {"1.0.0", "1.1.0"}
CORE_LINK_IDS = (
    "body-weight",
    "nausea-present",
    "nausea-severity",
    "vomiting-count-24h",
    "fluid-intake-24h-estimated",
    "abdominal-pain-present",
)
UNKNOWN_ALLOWED_LINK_IDS = ("fluid-intake-24h-estimated",)
OPENING_PROMPT = (
    "今天感觉怎么样？请按医生本轮确认的随访项目告诉我；"
    "没有提到的部分，我会再逐项询问。"
)


class PatientCheckinProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str
    answered_link_ids: tuple[str, ...]
    explicit_unknown_link_ids: tuple[str, ...] = ()
    missing_link_ids: tuple[str, ...]
    next_link_id: str | None = None
    next_prompt: str | None = None
    ready_to_submit: bool = False


class PatientChatFocus(BaseModel):
    """Deterministic field scope for one patient chat turn."""

    model_config = ConfigDict(frozen=True)

    link_ids: tuple[str, ...]
    mode: str
    prior_source_run_id: str | None = None


def project_patient_checkin(
    session: CareSession,
    questionnaire: dict[str, Any],
    *,
    explicit_unknown_link_ids: set[str] | frozenset[str] = frozenset(),
    collection_link_ids: tuple[str, ...] = CORE_LINK_IDS,
) -> PatientCheckinProjection:
    """Project completion and the next question from locked Pathway facts only."""

    if (
        (session.pathway_code, session.pathway_version) not in SUPPORTED_PATHWAYS
        or session.questionnaire_version not in SUPPORTED_QUESTIONNAIRE_VERSIONS
    ):
        raise ValueError("当前随访版本没有匹配的患者采集策略")
    policy_version = collection_policy_version(session)
    if any(link_id not in CORE_LINK_IDS for link_id in collection_link_ids):
        raise ValueError("当前医生方案包含未受控的患者采集字段")
    if not collection_link_ids:
        return PatientCheckinProjection(
            policy_version=policy_version,
            answered_link_ids=(),
            missing_link_ids=(),
            ready_to_submit=False,
        )
    collection_set = set(collection_link_ids)
    visible = {
        item["linkId"]: item
        for item in visible_questionnaire_items(questionnaire, session.answers)
        if item.get("linkId") in collection_set
    }
    answered = tuple(
        link_id
        for link_id in collection_link_ids
        if link_id in visible and _has_answer(session.answers.get(link_id))
    )
    unknown = tuple(
        link_id
        for link_id in UNKNOWN_ALLOWED_LINK_IDS
        if link_id in visible and link_id in explicit_unknown_link_ids
    )
    missing = tuple(
        link_id
        for link_id in collection_link_ids
        if link_id in visible
        and not _has_answer(session.answers.get(link_id))
        and link_id not in unknown
    )
    next_link = missing[0] if missing else None
    next_prompt = visible[next_link].get("text") if next_link else None
    return PatientCheckinProjection(
        policy_version=policy_version,
        answered_link_ids=answered,
        explicit_unknown_link_ids=unknown,
        missing_link_ids=missing,
        next_link_id=next_link,
        next_prompt=next_prompt,
        ready_to_submit=(
            bool(answered)
            and _has_answer(session.answers.get("free-text-report"))
            and not missing
        ),
    )


_CORRECTION_MARKER = re.compile(
    r"(?:刚才|刚刚|前面|之前|上面|说错|记错|更正|改成|改为|应该是|其实|不是[^，。；]{0,12}是)"
)
_FIELD_PATTERNS = {
    "body-weight": re.compile(r"(?:体重|公斤|千克|kg)", re.IGNORECASE),
    "vomiting-count-24h": re.compile(r"(?:呕吐|吐了|吐过|吐的?次数)"),
    "fluid-intake-24h-estimated": re.compile(
        r"(?:饮水|喝水|喝了|液体|毫升|ml|mL|升水|摄入量)"
    ),
    "abdominal-pain-present": re.compile(r"(?:腹痛|肚子痛|肚痛)"),
}
_NAUSEA_PATTERN = re.compile(r"(?:恶心|反胃|想吐)")
_NAUSEA_SEVERITY_PATTERN = re.compile(
    r"(?:恶心[^，。；]{0,8}(?:程度|轻度|轻微|中度|严重|重度)|"
    r"(?:轻度|轻微|中度|严重|重度)[^，。；]{0,8}恶心)"
)


def resolve_patient_chat_focus(
    session: CareSession,
    *,
    message_text: str,
    default_link_id: str | None,
    selected_revision_link_id: str | None = None,
    active_contexts: Iterable[Any],
    run_ids_newest_first: Iterable[str],
    collection_resolutions: dict[str, str] | None = None,
    collection_link_ids: tuple[str, ...] = CORE_LINK_IDS,
) -> PatientChatFocus:
    """Resolve one turn to governed fields without letting the model choose scope.

    Ordinary short answers inherit the deterministic next Questionnaire item.  A
    correction may target another already-confirmed item, but an implicit "刚才"
    reference is accepted only when the latest still-active confirmation event
    contains exactly one governed field.
    """

    text = message_text.strip()
    if not text:
        raise ValueError("请先输入本次合成随访回答")
    allowed_links = tuple(
        link_id for link_id in CORE_LINK_IDS if link_id in set(collection_link_ids)
    )
    allowed_set = set(allowed_links)
    if not allowed_links:
        raise ValueError("当前医生方案没有可由患者网页采集的指标")
    if default_link_id is not None and default_link_id not in allowed_set:
        raise ValueError("当前问题不属于锁定的患者采集策略")
    if (
        selected_revision_link_id is not None
        and selected_revision_link_id not in allowed_set
    ):
        raise ValueError("点选的修改项目不属于锁定的患者采集策略")

    current_resolutions = collection_resolutions or {}
    recorded_links = {
        link_id for link_id in allowed_links if link_id in session.answers
    } | {
        link_id
        for link_id, resolution in current_resolutions.items()
        if link_id in allowed_set and resolution == "explicit_unknown"
    }
    explicit_targets = _explicit_field_targets(text)
    if explicit_targets - allowed_set:
        raise ValueError("输入包含不在本轮医生方案内的指标，请按当前随访项目回答")
    correction = bool(_CORRECTION_MARKER.search(text))

    # A field selected by the patient in the final-review UI is an explicit
    # instruction, not an inference from the latest conversational turn.  The
    # text still fails closed when it explicitly names a different field.
    if selected_revision_link_id is not None:
        if selected_revision_link_id not in recorded_links:
            raise ValueError("要修改的指标尚未确认，请先完成当前采集")
        if len(explicit_targets) > 1:
            raise ValueError("一次只能修改一个已确认指标，请分开说明")
        if explicit_targets and selected_revision_link_id not in explicit_targets:
            raise ValueError("输入内容与点选的修改字段不一致，请分开说明")
        prior = _active_context_for_link(
            active_contexts, selected_revision_link_id
        )
        if selected_revision_link_id in session.answers and prior is None:
            raise ValueError("该指标缺少可验证的上一版来源，不能直接修改")
        return PatientChatFocus(
            link_ids=(selected_revision_link_id,),
            mode="revision",
            prior_source_run_id=(getattr(prior, "source_run_id", None)),
        )

    if not recorded_links:
        # The opening composer accepts a substantive, explicitly named report
        # across multiple Pathway fields.  A bare reply inherits the one question
        # visibly asked by the server, so "有"/"中度"/"2次" cannot float across
        # every governed item merely because this is the first turn.
        if default_link_id is None or (explicit_targets - {default_link_id}):
            return PatientChatFocus(link_ids=allowed_links, mode="opening")
        return PatientChatFocus(
            link_ids=(default_link_id,), mode="next_question"
        )

    # On the final review screen there is no "next missing" field.  A message
    # that explicitly names one existing field is therefore a correction even
    # when the patient does not use the word "更正".
    if explicit_targets and (correction or default_link_id is None):
        if len(explicit_targets) != 1:
            raise ValueError("一次只能修改一个已确认指标，请分开说明")
        link_id = next(iter(explicit_targets))
        if (
            default_link_id in recorded_links
            and default_link_id is not None
            and link_id != default_link_id
        ):
            raise ValueError("输入内容与点选的修改字段不一致，请分开说明")
        if link_id not in recorded_links:
            raise ValueError("要修改的指标尚未确认，请先回答当前问题")
        prior = _active_context_for_link(active_contexts, link_id)
        if link_id in session.answers and prior is None:
            raise ValueError("该指标缺少可验证的上一版来源，不能直接修改")
        return PatientChatFocus(
            link_ids=(link_id,),
            mode="revision",
            prior_source_run_id=(getattr(prior, "source_run_id", None)),
        )

    if correction:
        grouped: dict[str, list[Any]] = defaultdict(list)
        for context in active_contexts:
            link_id = getattr(context, "link_id", None)
            source_run_id = getattr(context, "source_run_id", None)
            if (
                link_id in CORE_LINK_IDS
                and link_id in recorded_links
                and source_run_id
            ):
                grouped[source_run_id].append(context)
        latest_group: list[Any] | None = None
        latest_run_id: str | None = None
        for run_id in run_ids_newest_first:
            if grouped.get(run_id):
                latest_group = grouped[run_id]
                latest_run_id = run_id
                break
        unique_links = {
            getattr(item, "link_id", None) for item in (latest_group or [])
        }
        unique_links.discard(None)
        if len(unique_links) != 1:
            raise ValueError("请说清要修改哪个指标，例如“把呕吐次数改成2次”")
        return PatientChatFocus(
            link_ids=(next(iter(unique_links)),),
            mode="revision",
            prior_source_run_id=latest_run_id,
        )

    if default_link_id is not None:
        prior = _active_context_for_link(active_contexts, default_link_id)
        return PatientChatFocus(
            link_ids=(default_link_id,),
            mode=("revision" if default_link_id in recorded_links else "next_question"),
            prior_source_run_id=(getattr(prior, "source_run_id", None)),
        )

    raise ValueError("请说清要修改哪个指标，或先点选需要修改的项目")


def questionnaire_answer_display(
    questionnaire: dict[str, Any], link_id: str, answer: Any
) -> str:
    """Render one governed draft answer without changing its stored value."""

    item = _questionnaire_item(questionnaire, link_id)
    if item is None:
        return str(answer)
    if isinstance(answer, bool):
        return "是" if answer else "否"
    if isinstance(answer, dict) and "value" in answer:
        unit = answer.get("unit", "")
        if unit == "mL" or answer.get("code") == "mL":
            unit = "毫升"
        return f"{answer['value']} {unit}".strip()
    choice_code = answer.get("code") if isinstance(answer, dict) else answer
    for option in item.get("answerOption", []):
        coding = option.get("valueCoding", {})
        if coding.get("code") == choice_code:
            return {
                "Mild": "轻度",
                "Moderate": "中度",
                "Severe": "重度",
            }.get(coding.get("display"), coding.get("display") or str(answer))
    return str(answer)


def questionnaire_candidate_confirmation_display(
    questionnaire: dict[str, Any], link_id: str, answer: Any
) -> tuple[str, str]:
    """Return an unambiguous patient-facing question and normalized answer."""

    item = _questionnaire_item(questionnaire, link_id)
    question = (
        str(item.get("text") or link_id) if item is not None else link_id
    )
    return question, questionnaire_answer_display(questionnaire, link_id, answer)


def questionnaire_choice_options(
    questionnaire: dict[str, Any], link_id: str
) -> tuple[tuple[str, str], ...]:
    """Return governed code/label pairs for one Questionnaire choice item."""

    item = _questionnaire_item(questionnaire, link_id)
    if item is None or item.get("type") != "choice":
        return ()
    return tuple(
        (
            coding["code"],
            questionnaire_answer_display(questionnaire, link_id, coding["code"]),
        )
        for option in item.get("answerOption", [])
        if isinstance((coding := option.get("valueCoding")), dict)
        and isinstance(coding.get("code"), str)
    )


def exact_questionnaire_choice_code(
    questionnaire: dict[str, Any], link_id: str, patient_text: str
) -> str | None:
    """Return the one governed code named exactly by a patient's choice text."""

    item = _questionnaire_item(questionnaire, link_id)
    if item is None or item.get("type") != "choice":
        return None
    selected_text = re.sub(r"[，。！？、\s]+", "", patient_text)
    matches: set[str] = set()
    for option in item.get("answerOption", []):
        coding = option.get("valueCoding", {})
        code = coding.get("code")
        labels = {
            str(coding.get("display") or ""),
            questionnaire_answer_display(questionnaire, link_id, code),
            *(
                str(extension.get("valueString") or "")
                for extension in option.get("extension", [])
                if extension.get("url")
                == "urn:continucare:StructureDefinition:answer-semantic-alias"
            ),
        }
        normalized = {
            re.sub(r"[，。！？、\s]+", "", label)
            for label in labels
            if label
        }
        if isinstance(code, str) and selected_text and selected_text in normalized:
            matches.add(code)
    return next(iter(matches)) if len(matches) == 1 else None


_FOCUSED_BOOLEAN_ALIASES: dict[str, dict[bool, frozenset[str]]] = {
    "nausea-present": {
        True: frozenset(),
        False: frozenset({"不恶心", "没恶心", "没有恶心", "不反胃", "没反胃", "没有反胃"}),
    },
    "abdominal-pain-present": {
        True: frozenset(),
        False: frozenset(
            {
                "不痛",
                "不疼",
                "没痛",
                "没疼",
                "没有痛",
                "没有疼",
                "肚子不痛",
                "肚子不疼",
                "肚子没痛",
                "肚子没疼",
                "没腹痛",
                "无腹痛",
                "没有腹痛",
            }
        ),
    },
}
_GENERIC_FOCUSED_BOOLEAN_ALIASES: dict[bool, frozenset[str]] = {
    True: frozenset({"是", "是的", "有", "有的", "对", "对的", "嗯", "嗯嗯"}),
    False: frozenset({"否", "无", "没有", "没有的", "没", "不是"}),
}


def exact_focused_boolean_answer(link_id: str, patient_text: str) -> bool | None:
    """Resolve a controlled colloquial yes/no only inside one visible boolean item."""

    selected_text = re.sub(r"[，。！？、\s]+", "", patient_text)
    matches = {
        answer
        for answer in (True, False)
        if selected_text
        in (
            _GENERIC_FOCUSED_BOOLEAN_ALIASES[answer]
            | _FOCUSED_BOOLEAN_ALIASES.get(link_id, {}).get(answer, frozenset())
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def exact_questionnaire_direct_answer(
    questionnaire: dict[str, Any], link_id: str, patient_text: str
) -> str | bool | None:
    """Return one exact governed choice or focused boolean answer."""

    item = _questionnaire_item(questionnaire, link_id)
    if item is None:
        return None
    if item.get("type") == "choice":
        return exact_questionnaire_choice_code(questionnaire, link_id, patient_text)
    if item.get("type") == "boolean":
        return exact_focused_boolean_answer(link_id, patient_text)
    return None


def is_single_focused_patient_checkin_task(task_id: str, link_id: str) -> bool:
    """Verify the immutable task identity for one focused patient-checkin field."""

    if not task_id.startswith("patient-checkin:") or task_id.count(":focus:") != 1:
        return False
    focus_digest = hashlib.sha256(
        json.dumps([link_id], ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return task_id.endswith(f":focus:{focus_digest}")


def _questionnaire_item(
    questionnaire: dict[str, Any], link_id: str
) -> dict[str, Any] | None:
    return next(
        (
            candidate
            for candidate in _flatten_items(questionnaire.get("item", []))
            if candidate.get("linkId") == link_id
        ),
        None,
    )


def _explicit_field_targets(text: str) -> set[str]:
    targets = {
        link_id for link_id, pattern in _FIELD_PATTERNS.items() if pattern.search(text)
    }
    if _NAUSEA_PATTERN.search(text):
        targets.add(
            "nausea-severity"
            if _NAUSEA_SEVERITY_PATTERN.search(text)
            else "nausea-present"
        )
    return targets


def _active_context_for_link(active_contexts: Iterable[Any], link_id: str) -> Any | None:
    matches = [
        item for item in active_contexts if getattr(item, "link_id", None) == link_id
    ]
    if len(matches) > 1:
        raise ValueError("同一指标存在多个当前来源，不能继续修改")
    return matches[0] if matches else None


def _flatten_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten_items(item.get("item", [])))
    return flattened


_DIRECT_IDENTIFIER_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    re.compile(r"https?://", re.IGNORECASE),
)
_INJECTION_MARKERS = (
    "忽略之前",
    "忽略以上",
    "system prompt",
    "developer message",
    "api key",
    "password",
    "我叫",
    "姓名",
    "住址",
    "地址",
    "病历号",
    "病案号",
    "医保号",
    "护照",
    "身份证",
    "电话号码",
    "手机号",
    "微信号",
)


def validate_synthetic_chat_message(
    message_text: str,
    *,
    synthetic_confirmed: bool,
) -> str:
    """Fail closed before any external request or live persistence."""

    text = message_text.strip()
    if not synthetic_confirmed:
        raise ValueError("请先确认本页只用于合成演示，不输入真实患者信息")
    if not text:
        raise ValueError("请先输入本次合成随访回答")
    if len(text) > 500 or "\x00" in text:
        raise ValueError("演示回答过长或包含不支持的内容，请缩短后重试")
    lowered = text.lower()
    if any(pattern.search(text) for pattern in _DIRECT_IDENTIFIER_PATTERNS) or any(
        marker in lowered for marker in _INJECTION_MARKERS
    ):
        raise ValueError("检测到标识符、链接或指令性内容；该文本未发送，也未保存")
    return text


def record_explicit_unknown(store, session: CareSession, link_id: str) -> CareSession:
    """Record that the synthetic patient cannot estimate one allowed metric."""

    if link_id not in UNKNOWN_ALLOWED_LINK_IDS:
        raise ValueError("当前指标不能用“不确定”代替回答")
    now = utc_now_iso()
    policy_version = collection_policy_version(session)
    identity = uuid5(
        NAMESPACE_URL,
        f"{session.session_id}|{link_id}|explicit_unknown|{session.updated_at}",
    ).hex
    audit = build_audit_event(
        event_id=f"audit-{identity}",
        patient_id=session.patient_id,
        entity_type="CareSession",
        entity_id=session.session_id,
        event_type="patient_collection_explicit_unknown",
        actor_type="synthetic_patient",
        created_at=now,
        details={
            "link_id": link_id,
            "resolution": "explicit_unknown",
            "collection_policy_version": policy_version,
            "clinical_assessment": "not_assessed",
        },
    )
    return store.record_explicit_unknown(
        expected_session=session,
        link_id=link_id,
        policy_version=policy_version,
        resolution_id=f"collection-resolution-{identity}",
        resolved_at=now,
        audit_event=audit,
    )


def collection_policy_version(session: CareSession) -> str:
    """Return the collection policy locked to this session's Questionnaire."""

    version = COLLECTION_POLICY_VERSIONS.get(session.questionnaire_version)
    if version is None:
        raise ValueError("当前问卷版本没有匹配的采集策略")
    return version


def _has_answer(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, dict) and "value" in value:
        return value.get("value") is not None
    return True
