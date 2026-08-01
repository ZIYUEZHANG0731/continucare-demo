"""Deterministic semantic mock for synthetic Layer-3 evaluation and fallback."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from continucare.agents.contracts import (
    ClarificationKind,
    ClarificationOption,
    ClarificationRequest,
    SemanticCandidate,
    SemanticResult,
    SemanticStatus,
    SemanticTask,
    Temporality,
)
from continucare.care_agent.language import PatientLanguageRenderer
from continucare.care_agent.safety import instruction_like_text
from continucare.db import utc_now_iso
from continucare.fhir.terminology import UCUM


_ZH_NUMBERS = {
    "零": 0,
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
_CURRENT_MARKERS = re.compile(r"现在|目前|此刻|今天|刚才|刚刚")
_HISTORICAL_MARKERS = re.compile(r"上周|上个月|去年|以前|之前|治疗前|很久前")
_OTHER_SUBJECT = re.compile(r"我(?:妈妈|妈|爸爸|爸|父亲|母亲|孩子|朋友|家人)|(?:他|她|家人|朋友|孩子)")
_VOMITING = re.compile(r"(?:呕吐|吐了?|吐过)\s*(?P<count>\d+|[零一二两三四五六七八九十])\s*次")
_FLUID = re.compile(
    r"(?:喝水|饮水|液体(?:摄入)?)\D{0,8}(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>毫升|ml|mL|升|L)",
    re.IGNORECASE,
)


class DeterministicSemanticMock:
    VERSION = "care-semantic-mock-v1"
    mode = "local_semantic_mock"

    def __init__(self, language: PatientLanguageRenderer | None = None):
        self.language = language or PatientLanguageRenderer.load_builtin()

    def extract(self, task: SemanticTask) -> SemanticResult:
        run_id = _stable_id("run", task.task_id)
        if instruction_like_text(task.message_text):
            return SemanticResult(
                run_id=run_id,
                task_id=task.task_id,
                status=SemanticStatus.BLOCKED,
                mode=self.mode,
                care_agent_version=self.VERSION,
                safety_agent_version="pending",
                language_policy_version=self.language.version,
                ignored_reasons=[
                    "instruction_like_text",
                    self.language.render("blocked_instruction"),
                ],
                completed_at=utc_now_iso(),
            )

        candidates: list[SemanticCandidate] = []
        clarifications: list[ClarificationRequest] = []
        ignored: list[str] = []
        self._extract_vomiting(task, candidates, clarifications, ignored)
        self._extract_fluid(task, candidates, clarifications, ignored)
        self._extract_symptom(
            task,
            phrase="恶心",
            link_id="nausea-present",
            candidates=candidates,
            clarifications=clarifications,
            ignored=ignored,
        )
        self._extract_symptom(
            task,
            phrase="腹痛",
            link_id="abdominal-pain-present",
            alternatives=("肚子痛", "肚子疼"),
            candidates=candidates,
            clarifications=clarifications,
            ignored=ignored,
        )
        self._extract_nausea_severity(task, candidates, ignored)
        candidates.sort(key=lambda item: (item.evidence_start, item.link_id))
        clarifications.sort(
            key=lambda item: (
                item.proposed_candidate.evidence_start
                if item.proposed_candidate
                else len(task.message_text)
            )
        )
        status = (
            SemanticStatus.NEEDS_CLARIFICATION
            if clarifications
            else SemanticStatus.NEEDS_CONFIRMATION
            if candidates
            else SemanticStatus.NO_MATCH
        )
        if status == SemanticStatus.NO_MATCH:
            ignored.append(self.language.render("no_structured_fact"))
        return SemanticResult(
            run_id=run_id,
            task_id=task.task_id,
            status=status,
            mode=self.mode,
            care_agent_version=self.VERSION,
            safety_agent_version="pending",
            language_policy_version=self.language.version,
            candidates=candidates,
            clarifications=clarifications,
            ignored_reasons=list(dict.fromkeys(ignored)),
            completed_at=utc_now_iso(),
        )

    def _extract_vomiting(self, task, candidates, clarifications, ignored) -> None:
        matches = list(_VOMITING.finditer(task.message_text))
        usable = [match for match in matches if not _excluded_context(task.message_text, match, ignored)]
        values = {_number(match.group("count")) for match in usable}
        if len(values) > 1:
            clarifications.append(
                ClarificationRequest(
                    clarification_id=_stable_id("clarify", task.task_id, "vomit-conflict"),
                    kind=ClarificationKind.RESOLVE_CONFLICT,
                    prompt=self.language.render("resolve_conflict"),
                    options=_non_accepting_options(self.language),
                )
            )
            return
        if not usable:
            return
        match = usable[0]
        count = _number(match.group("count"))
        context = _sentence(task.message_text, match.start(), match.end())
        explicit_24h = _has_24h(context.text)
        template_id = "confirm_explicit_count" if explicit_24h else "confirm_time_window_count"
        candidate = self._candidate(
            task,
            link_id="vomiting-count-24h",
            answer=count,
            match=match,
            temporality=Temporality.EXPLICIT_24H if explicit_24h else Temporality.UNSPECIFIED,
            template_id=template_id,
            message=self.language.render(template_id, value=count),
        )
        if explicit_24h:
            candidates.append(candidate)
        else:
            clarifications.append(
                self._clarification(
                    task,
                    kind=ClarificationKind.CONFIRM_TIME_WINDOW,
                    candidate=candidate,
                    prompt=candidate.patient_message,
                    yes_option="yes_24h",
                )
            )

    def _extract_fluid(self, task, candidates, clarifications, ignored) -> None:
        for match in _FLUID.finditer(task.message_text):
            if _excluded_context(task.message_text, match, ignored):
                continue
            amount = float(match.group("amount"))
            if match.group("unit") in {"升", "L", "l"}:
                amount *= 1000
            amount = int(amount) if amount.is_integer() else amount
            context = _sentence(task.message_text, match.start(), match.end())
            explicit_24h = _has_24h(context.text)
            template_id = (
                "confirm_explicit_quantity"
                if explicit_24h
                else "confirm_time_window_quantity"
            )
            candidate = self._candidate(
                task,
                link_id="fluid-intake-24h-estimated",
                answer={
                    "value": amount,
                    "unit": "mL",
                    "system": UCUM,
                    "code": "mL",
                },
                match=match,
                temporality=(
                    Temporality.EXPLICIT_24H
                    if explicit_24h
                    else Temporality.UNSPECIFIED
                ),
                template_id=template_id,
                message=self.language.render(template_id, value=amount),
            )
            if explicit_24h:
                candidates.append(candidate)
            else:
                clarifications.append(
                    self._clarification(
                        task,
                        kind=ClarificationKind.CONFIRM_TIME_WINDOW,
                        candidate=candidate,
                        prompt=candidate.patient_message,
                        yes_option="yes_24h",
                    )
                )
            break

    def _extract_symptom(
        self,
        task,
        *,
        phrase,
        link_id,
        candidates,
        clarifications,
        ignored,
        alternatives=(),
    ) -> None:
        pattern = re.compile("|".join(map(re.escape, (phrase, *alternatives))))
        match = pattern.search(task.message_text)
        if not match or _excluded_context(
            task.message_text, match, ignored, allow_negated=True
        ):
            return
        context = _sentence(task.message_text, match.start(), match.end())
        current = bool(_CURRENT_MARKERS.search(context.text))
        negated = bool(
            re.search(
                r"(?:没有|没|未|无|不)\s*$",
                task.message_text[max(context.start, match.start() - 4) : match.start()],
            )
        )
        answer = not negated
        if current:
            template_id = "confirm_boolean_present" if answer else "confirm_boolean_absent"
            message = self.language.render(template_id, symptom=phrase)
        else:
            template_id = "confirm_current_symptom"
            message = self.language.render(template_id, symptom=phrase)
        evidence_match = _SpanMatch(task.message_text, context.start, context.end)
        candidate = self._candidate(
            task,
            link_id=link_id,
            answer=answer,
            match=evidence_match,
            temporality=Temporality.CURRENT if current else Temporality.UNSPECIFIED,
            template_id=template_id,
            message=message,
            negated=negated,
        )
        if current:
            candidates.append(candidate)
        else:
            clarifications.append(
                self._clarification(
                    task,
                    kind=ClarificationKind.CONFIRM_CURRENT,
                    candidate=candidate,
                    prompt=message,
                    yes_option="yes_current",
                )
            )

    def _extract_nausea_severity(self, task, candidates, ignored) -> None:
        nausea_candidates = [item for item in candidates if item.link_id == "nausea-present" and item.answer is True]
        if not nausea_candidates:
            return
        context = _sentence(
            task.message_text,
            nausea_candidates[0].evidence_start,
            nausea_candidates[0].evidence_end,
        )
        severity = None
        display = None
        for pattern, code, label in (
            (r"轻微|轻度|有点", "LA6752-5", "轻度"),
            (r"中度|比较明显", "LA6751-7", "中度"),
            (r"严重|重度|非常", "LA6750-9", "重度"),
        ):
            if re.search(pattern, context.text):
                severity, display = code, label
                break
        if severity is None:
            return
        candidate = self._candidate(
            task,
            link_id="nausea-severity",
            answer=severity,
            match=_SpanMatch(task.message_text, context.start, context.end),
            temporality=Temporality.CURRENT,
            template_id="confirm_severity",
            message=self.language.render(
                "confirm_severity", symptom="恶心", severity=display
            ),
        )
        candidates.append(candidate)

    def _candidate(
        self,
        task,
        *,
        link_id,
        answer,
        match,
        temporality,
        template_id,
        message,
        negated=False,
    ) -> SemanticCandidate:
        item = next(item for item in task.allowed_items if item.link_id == link_id)
        answer_key = json.dumps(answer, ensure_ascii=False, sort_keys=True)
        return SemanticCandidate(
            candidate_id=_stable_id(
                "candidate", task.task_id, link_id, str(match.start()), answer_key
            ),
            link_id=link_id,
            answer=answer,
            questionnaire_code=item.codes[0] if item.codes else None,
            evidence_text=task.message_text[match.start() : match.end()],
            evidence_start=match.start(),
            evidence_end=match.end(),
            temporality=temporality,
            negated=negated,
            patient_message=message,
            template_id=template_id,
        )

    def _clarification(self, task, *, kind, candidate, prompt, yes_option):
        return ClarificationRequest(
            clarification_id=_stable_id("clarify", candidate.candidate_id),
            kind=kind,
            prompt=prompt,
            proposed_candidate=candidate,
            options=[
                ClarificationOption(
                    option_id=yes_option,
                    label=self.language.option(yes_option),
                    accepts_candidate=True,
                ),
                ClarificationOption(
                    option_id="no", label=self.language.option("no")
                ),
                ClarificationOption(
                    option_id="unsure", label=self.language.option("unsure")
                ),
            ],
        )


class _SpanMatch:
    def __init__(self, text: str, start: int, end: int):
        self.text = text
        self._start = start
        self._end = end

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end


class _Sentence:
    def __init__(self, text: str, start: int, end: int):
        self.text = text
        self.start = start
        self.end = end


def _sentence(text: str, start: int, end: int) -> _Sentence:
    left = max(text.rfind(mark, 0, start) for mark in "。！？；\n") + 1
    right_candidates = [text.find(mark, end) for mark in "。！？；\n"]
    right_candidates = [value for value in right_candidates if value >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    return _Sentence(text[left:right], left, right)


def _excluded_context(
    text: str, match, ignored: list[str], *, allow_negated: bool = False
) -> bool:
    context = _sentence(text, match.start(), match.end())
    if _OTHER_SUBJECT.search(context.text):
        ignored.append("other_person_subject_ignored")
        return True
    if _HISTORICAL_MARKERS.search(context.text) and not _has_24h(context.text):
        ignored.append("historical_statement_ignored")
        return True
    prefix = text[max(context.start, match.start() - 5) : match.start()]
    if not allow_negated and re.search(r"(?:没有|没|未|无|不)\s*$", prefix):
        ignored.append("negated_quantity_ignored")
        return True
    return False


def _has_24h(text: str) -> bool:
    return bool(re.search(r"(?:过去|近|最近)?\s*24\s*(?:小时|h)(?:内)?", text, re.IGNORECASE))


def _number(value: str) -> int:
    return int(value) if value.isdigit() else _ZH_NUMBERS[value]


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, '|'.join(parts)).hex}"


def _non_accepting_options(language: PatientLanguageRenderer):
    return [
        ClarificationOption(option_id="no", label="前往完整问卷确认"),
        ClarificationOption(option_id="unsure", label=language.option("unsure")),
    ]
