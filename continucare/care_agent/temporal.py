"""Deterministic patient-local temporal context for Layer-3 turns."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from continucare.agents.contracts import (
    SemanticCandidate,
    TemporalContext,
    TemporalKind,
    TemporalMention,
    TemporalResolution,
    TemporalResolutionBasis,
    Temporality,
)


_MENTION_PATTERNS = (
    (
        re.compile(r"(?:过去|近|最近)?\s*24\s*(?:小时|h)(?:内)?", re.IGNORECASE),
        TemporalKind.ROLLING_24H,
    ),
    (re.compile(r"昨天|昨日"), TemporalKind.LOCAL_CALENDAR_DAY),
    (re.compile(r"今天|今日"), TemporalKind.PARTIAL_LOCAL_DAY),
    (re.compile(r"现在|目前|此刻|刚才|刚刚"), TemporalKind.POINT_IN_TIME),
)


def build_temporal_context(
    *,
    session_id: str,
    session_created_at: str,
    message_text: str,
    patient_timezone: str,
    received_at: str,
) -> TemporalContext:
    """Anchor relative expressions to the patient's local clock, never server time."""

    zone = _zone(patient_timezone)
    received = _aware_datetime(received_at).astimezone(timezone.utc)
    received_local = received.astimezone(zone)
    scheduled = _aware_datetime(session_created_at).astimezone(timezone.utc)
    scheduled_local = scheduled.astimezone(zone)
    rolling_start = received_local - timedelta(hours=24)
    occurrence_id = (
        "occurrence-"
        + uuid5(NAMESPACE_URL, f"{session_id}|{scheduled_local.date().isoformat()}").hex
    )
    return TemporalContext(
        patient_timezone=patient_timezone,
        received_at_utc=received.isoformat(),
        received_at_local=received_local.isoformat(),
        local_date=received_local.date().isoformat(),
        scheduled_at=scheduled.isoformat(),
        scheduled_local_date=scheduled_local.date().isoformat(),
        followup_occurrence_id=occurrence_id,
        rolling_24h_start=rolling_start.isoformat(),
        rolling_24h_end=received_local.isoformat(),
        detected_mentions=_mentions(message_text, received_local),
    )


def candidate_temporal_resolution(
    candidate: SemanticCandidate,
    context: TemporalContext,
    *,
    basis: TemporalResolutionBasis = TemporalResolutionBasis.EXPLICIT_PATIENT_TEXT,
    inherited_from_action_id: str | None = None,
) -> TemporalResolution | None:
    """Convert a governed temporality label into an auditable effective interval."""

    expression = _candidate_expression(candidate, context)
    if candidate.temporality == Temporality.EXPLICIT_24H:
        return TemporalResolution(
            kind=TemporalKind.ROLLING_24H,
            expression=expression,
            effective_start=context.rolling_24h_start,
            effective_end=context.rolling_24h_end,
            timezone=context.patient_timezone,
            anchor_at=context.received_at_local,
            basis=basis,
            inherited_from_action_id=inherited_from_action_id,
        )
    if candidate.temporality == Temporality.CURRENT:
        return TemporalResolution(
            kind=TemporalKind.POINT_IN_TIME,
            expression=expression,
            effective_start=context.received_at_local,
            effective_end=context.received_at_local,
            timezone=context.patient_timezone,
            anchor_at=context.received_at_local,
            basis=basis,
            inherited_from_action_id=inherited_from_action_id,
        )
    if candidate.temporality == Temporality.HISTORICAL:
        mention = next(
            (
                item
                for item in context.detected_mentions
                if item.kind == TemporalKind.LOCAL_CALENDAR_DAY
            ),
            None,
        )
        if mention is not None:
            return TemporalResolution(
                kind=mention.kind,
                expression=mention.expression,
                effective_start=mention.effective_start,
                effective_end=mention.effective_end,
                timezone=context.patient_timezone,
                anchor_at=context.received_at_local,
                basis=basis,
                inherited_from_action_id=inherited_from_action_id,
            )
    return None


def _mentions(text_value: str, received_local: datetime) -> list[TemporalMention]:
    occupied: list[tuple[int, int]] = []
    mentions: list[TemporalMention] = []
    for pattern, kind in _MENTION_PATTERNS:
        for match in pattern.finditer(text_value):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            start, end = _interval(kind, received_local)
            mentions.append(
                TemporalMention(
                    expression=match.group(0),
                    kind=kind,
                    effective_start=start.isoformat(),
                    effective_end=end.isoformat(),
                    evidence_start=match.start(),
                    evidence_end=match.end(),
                )
            )
            occupied.append((match.start(), match.end()))
    return sorted(mentions, key=lambda item: item.evidence_start)


def _interval(kind: TemporalKind, received_local: datetime) -> tuple[datetime, datetime]:
    if kind == TemporalKind.ROLLING_24H:
        return received_local - timedelta(hours=24), received_local
    if kind == TemporalKind.LOCAL_CALENDAR_DAY:
        start = datetime.combine(
            received_local.date() - timedelta(days=1), time.min, received_local.tzinfo
        )
        return start, start + timedelta(days=1)
    if kind == TemporalKind.PARTIAL_LOCAL_DAY:
        start = datetime.combine(received_local.date(), time.min, received_local.tzinfo)
        return start, received_local
    return received_local, received_local


def _candidate_expression(
    candidate: SemanticCandidate, context: TemporalContext
) -> str | None:
    overlapping = [
        item
        for item in context.detected_mentions
        if item.evidence_start < candidate.evidence_end
        and item.evidence_end > candidate.evidence_start
    ]
    if not overlapping:
        overlapping = context.detected_mentions
    if candidate.temporality == Temporality.EXPLICIT_24H:
        wanted = {TemporalKind.ROLLING_24H}
    elif candidate.temporality == Temporality.CURRENT:
        wanted = {TemporalKind.POINT_IN_TIME, TemporalKind.PARTIAL_LOCAL_DAY}
    else:
        wanted = {TemporalKind.LOCAL_CALENDAR_DAY}
    return next(
        (item.expression for item in overlapping if item.kind in wanted),
        None,
    )


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间必须包含时区偏移")
    return parsed


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无效的患者时区: {name}") from exc
