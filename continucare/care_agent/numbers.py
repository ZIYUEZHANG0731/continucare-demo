"""Small deterministic number helpers for governed patient evidence."""

from __future__ import annotations

import re


_DIGITS = {
    "零": 0,
    "〇": 0,
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
}
_NUMBER_TOKEN = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)"


def parse_number_token(value) -> int | float | None:
    if type(value) is int:
        return value
    if type(value) is float:
        return int(value) if value.is_integer() else value
    if not isinstance(value, str):
        return None
    token = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        number = float(token)
        return int(number) if number.is_integer() else number
    if not re.fullmatch(r"[零〇一二两三四五六七八九十]+", token):
        return None
    if "十" not in token:
        if len(token) != 1:
            return None
        return _DIGITS.get(token)
    if token.count("十") != 1:
        return None
    left, right = token.split("十", 1)
    tens = 1 if not left else _DIGITS.get(left)
    ones = 0 if not right else _DIGITS.get(right)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def count_from_evidence(text: str) -> int | None:
    match = re.search(rf"({_NUMBER_TOKEN})\s*次", text)
    if match is None:
        return None
    value = parse_number_token(match.group(1))
    return value if type(value) is int else None


def millilitres_from_evidence(text: str) -> int | float | None:
    match = re.search(
        rf"({_NUMBER_TOKEN})\s*(毫升|ml|mL|升|l|L)",
        text,
    )
    if match is None:
        return None
    value = parse_number_token(match.group(1))
    if value is None:
        return None
    if match.group(2) in {"升", "l", "L"}:
        value *= 1000
    return int(value) if float(value).is_integer() else value
