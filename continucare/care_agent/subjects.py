"""Deterministic subject classification shared by semantic safety stages."""

from __future__ import annotations

import re

from continucare.agents.contracts import SubjectType


_OTHER_PERSON_PATTERN = re.compile(
    r"我(?:妈妈|妈|爸爸|爸|父亲|母亲|孩子|朋友|家人)|"
    r"(?:他|她|家人|朋友|孩子)"
)


def classify_evidence_subject(
    text: str, evidence_start: int, evidence_end: int
) -> SubjectType:
    """Classify the subject from the complete sentence around exact evidence."""

    left = max(text.rfind(mark, 0, evidence_start) for mark in "。！？；\n") + 1
    right_candidates = [text.find(mark, evidence_end) for mark in "。！？；\n"]
    right_candidates = [item for item in right_candidates if item >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    sentence = text[left:right]
    if _OTHER_PERSON_PATTERN.search(sentence):
        return SubjectType.OTHER_PERSON
    return SubjectType.PATIENT
