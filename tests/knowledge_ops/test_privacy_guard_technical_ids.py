from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from continucare.knowledge.ops import AppendOnlyLedger, LedgerCollection, LedgerRef
from continucare.knowledge.ops import acquisition as acquisition_module
from continucare.knowledge.ops import release as release_module
from continucare.knowledge.ops import review as review_module
from continucare.knowledge.ops.models import KnowledgeOpsPolicyError
from continucare.knowledge.ops.security import (
    AUDITED_SHA256_FIELDS,
    AUDITED_SHA256_FIELD_EVIDENCE,
    NUMERIC_SENSITIVE_PATTERNS,
    assert_no_sensitive_data,
    digest_derived_internal_id,
    maximum_unseparated_digit_run,
    min_sensitive_unseparated_digit_run,
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


def test_numeric_sensitive_patterns_expose_stable_separation_metadata() -> None:
    by_name = {item.name: item for item in NUMERIC_SENSITIVE_PATTERNS}
    assert by_name["cn_phone"].minimum_unseparated_digit_run == 11
    assert by_name["cn_national_id"].minimum_unseparated_digit_run == 18
    assert by_name["international_phone"].requires_leading_plus is True
    assert by_name["international_phone"].minimum_unseparated_digit_run is None
    assert min_sensitive_unseparated_digit_run == 11


@pytest.mark.parametrize(
    "value",
    [
        "event-13800138000aabbccdde",
        "attest-13800138000aabbccddeeff0011223344",
        "fixture-13800138000aabbccddeeff0011223344",
        "13800138000",
    ],
)
def test_ungrouped_phone_bearing_internal_ids_and_raw_phone_are_rejected(
    value: str,
) -> None:
    with pytest.raises(KnowledgeOpsPolicyError, match="appears to contain personal data"):
        assert_no_sensitive_data({"internal_id": value})


@pytest.mark.parametrize(
    ("prefix", "expected_hex_characters", "generator"),
    [
        (
            "event",
            20,
            lambda seed, digest, reference: review_module._event_id(
                f"review-seed-{seed}", datetime(2026, 8, 14, tzinfo=timezone.utc)
            ),
        ),
        (
            "attest",
            32,
            lambda seed, digest, reference: review_module._attestation_id(digest),
        ),
        (
            "fixture",
            32,
            lambda seed, digest, reference: digest_derived_internal_id(
                "fixture", digest, digest_characters=32
            ),
        ),
        (
            "packet",
            32,
            lambda seed, digest, reference: review_module._chain_id(
                "packet", reference, f"gate-{seed}"
            ),
        ),
        (
            "snapshot",
            32,
            lambda seed, digest, reference: acquisition_module._derived_id(
                "snapshot", f"source-{seed}"
            ),
        ),
        (
            "readiness",
            32,
            lambda seed, digest, reference: release_module._derived_id(
                "readiness", reference
            ),
        ),
    ],
)
def test_digest_derived_internal_id_generators_are_phone_safe_for_10000_seeds(
    prefix: str, expected_hex_characters: int, generator
) -> None:
    for seed in range(10_000):
        digest = hashlib.sha256(f"deterministic-seed-{seed}".encode()).hexdigest()
        reference = LedgerRef(
            collection=LedgerCollection.CANDIDATE,
            record_id=f"subject-{seed}",
            record_version=1,
            entry_sha256=digest,
        )
        identifier = generator(seed, digest, reference)
        assert identifier == generator(seed, digest, reference)
        assert maximum_unseparated_digit_run(identifier) < (
            min_sensitive_unseparated_digit_run
        )
        digest_groups = identifier.removeprefix(f"{prefix}-").split("-")
        assert all(1 <= len(group) <= 8 for group in digest_groups)
        assert all(re.fullmatch(r"[0-9a-f]+", group) for group in digest_groups)
        assert sum(len(group) for group in digest_groups) == expected_hex_characters
        assert_no_sensitive_data({"internal_id": identifier})


def test_valid_grouped_event_attestation_and_fixture_ids_require_no_exemption() -> None:
    digest = hashlib.sha256(b"phone-safe-id-regression").hexdigest()
    values = (
        digest_derived_internal_id("event", digest, digest_characters=20),
        digest_derived_internal_id("attest", digest, digest_characters=32),
        digest_derived_internal_id("fixture", digest, digest_characters=32),
    )
    assert values[0].count("-") == 3
    assert all(maximum_unseparated_digit_run(item) < 11 for item in values)
    assert_no_sensitive_data({"internal_ids": values})


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
