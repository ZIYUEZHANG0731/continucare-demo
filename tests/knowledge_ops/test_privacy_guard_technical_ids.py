from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from continucare.knowledge.ops import AppendOnlyLedger, LedgerCollection
from continucare.knowledge.ops.models import KnowledgeOpsPolicyError
from continucare.knowledge.ops.security import (
    AUDITED_SHA256_FIELDS,
    AUDITED_SHA256_FIELD_EVIDENCE,
    assert_no_sensitive_data,
)


PHONE_BEARING_HEX64 = "a" * 8 + "13800138000" + "b" * 45


def _walk(value: object, path: str = "$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def test_internal_review_ids_do_not_trigger_phone_number_false_positives() -> None:
    assert_no_sensitive_data(
        {
            "event_id": "event-17255792435b1b6f15e5",
            "attestation_id": "attest-17255792435b1b6f15e5aabbccddeeff",
        }
    )


def test_only_exact_audited_lowercase_sha256_fields_receive_digest_treatment() -> None:
    assert len(PHONE_BEARING_HEX64) == 64
    assert re.search(r"1[3-9]\d{9}", PHONE_BEARING_HEX64)
    assert AUDITED_SHA256_FIELDS == frozenset(AUDITED_SHA256_FIELD_EVIDENCE)
    assert all(AUDITED_SHA256_FIELD_EVIDENCE.values())
    for field in AUDITED_SHA256_FIELDS:
        assert_no_sensitive_data({field: PHONE_BEARING_HEX64})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("foo_sha256", PHONE_BEARING_HEX64),
        ("opaque_hex64", PHONE_BEARING_HEX64),
        ("entry_sha256", PHONE_BEARING_HEX64[:-1]),
        ("entry_sha256", PHONE_BEARING_HEX64[:-1] + "g"),
        ("entry_sha256", PHONE_BEARING_HEX64.upper()),
    ],
)
def test_unknown_or_malformed_digest_values_are_still_scanned(
    field: str, value: str
) -> None:
    with pytest.raises(KnowledgeOpsPolicyError, match="appears to contain personal data"):
        assert_no_sensitive_data({field: value})


@pytest.mark.parametrize(
    "value",
    [
        "13800138000",
        "patient@example.com",
        "11010519491231002X",
        "patient_id: synthetic-123",
    ],
)
def test_regular_sensitive_text_detection_is_unchanged(value: str) -> None:
    with pytest.raises(KnowledgeOpsPolicyError, match="appears to contain personal data"):
        assert_no_sensitive_data({"free_text": value})


def test_all_committed_knowledge_manifest_hex64_values_use_audited_fields() -> None:
    root = Path(__file__).parents[2] / "continucare" / "knowledge"
    manifest_roots = (root / "manifests", root / "manifests_v2")
    exact_hex64 = re.compile(r"^[0-9a-f]{64}$")
    hits: list[tuple[Path, str, str]] = []
    for manifest_root in manifest_roots:
        for path in sorted(manifest_root.glob("*.json")):
            for structured_path, value in _walk(json.loads(path.read_bytes())):
                if isinstance(value, str) and exact_hex64.fullmatch(value):
                    field = structured_path.rsplit(".", maxsplit=1)[-1]
                    hits.append((path, structured_path, field))
                    assert field in AUDITED_SHA256_FIELDS, (
                        path,
                        structured_path,
                        field,
                    )
    assert hits


def test_ledger_entry_digest_is_recomputed_from_canonical_entry(tmp_path: Path) -> None:
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    entry = ledger.append(
        LedgerCollection.GAP,
        "digest-recompute-proof",
        payload_type="digest_recompute_fixture",
        payload={"status": "synthetic"},
        recorded_by="system:test",
        synthetic=True,
    )
    stored = json.loads(
        (
            ledger.root
            / "records"
            / LedgerCollection.GAP.value
            / entry.record_id
            / "00000001.json"
        ).read_bytes()
    )
    claimed = stored.pop("entry_sha256")
    canonical = (
        json.dumps(
            stored,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    assert claimed == entry.entry_sha256 == hashlib.sha256(canonical).hexdigest()


def test_internal_id_exemption_does_not_weaken_forbidden_patient_keys() -> None:
    with pytest.raises(KnowledgeOpsPolicyError, match="data key is prohibited"):
        assert_no_sensitive_data({"patient_id": "event-17255792435b1b6f15e5"})
    with pytest.raises(KnowledgeOpsPolicyError, match="appears to contain personal data"):
        assert_no_sensitive_data({"event_id": "patient@example.com"})
