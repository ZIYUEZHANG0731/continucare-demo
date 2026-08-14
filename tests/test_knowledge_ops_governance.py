from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import continucare.knowledge as knowledge_v1
import continucare.knowledge.manifests_v2 as manifests_v2
from continucare.knowledge.ops import (
    AppendOnlyLedger,
    ClinicalContextScope,
    DirectoryBundleSource,
    GovernanceGate,
    KnowledgeOpsIntegrityError,
    KnowledgeOpsManifestError,
    LedgerCollection,
    SourceOperation,
    SourcePolicy,
    ValidationDomain,
    load_builtin_ops_bundle,
    load_builtin_ops_read_model,
    load_ops_bundle,
)


def _manifest_root() -> Path:
    assert manifests_v2.__file__ is not None
    return Path(manifests_v2.__file__).parent


def _copy_bundle(tmp_path: Path) -> Path:
    target = tmp_path / "bundle"
    shutil.copytree(_manifest_root(), target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _policy_payload(**overrides):
    payload = {
        "policy_id": "test-policy",
        "policy_version": 1,
        "display_name": "Test policy",
        "issuing_authority": "Synthetic authority",
        "source_types": ["terminology_standard"],
        "source_jurisdictions": [{"system": "global", "code": "GLOBAL"}],
        "languages": ["en"],
        "allowed_origins": ["https://example.org"],
        "allow_subdomains": False,
        "allowed_query_parameters": [],
        "allowed_content_types": ["text/html"],
        "maximum_response_bytes": 1024,
        "license_posture": "needs_verification",
        "terms_uri": None,
        "operation_rules": [
            {
                "operation": "register_link_metadata",
                "decision": "allow",
                "rationale": "Synthetic metadata fixture.",
            }
        ],
        "live_network_enabled": False,
        "status": "active",
        "registered_at": "2026-08-14T00:00:00+02:00",
        "registered_by": "test",
        "notes": [],
    }
    payload.update(overrides)
    return payload


def test_builtin_v2_bundle_is_complete_and_fail_closed():
    bundle = load_builtin_ops_bundle()

    assert bundle.index.bundle_id == "continucare-knowledge-ops"
    assert bundle.boundary.knowledge_effect == "informational_only"
    assert bundle.boundary.runtime_authority == "none"
    assert bundle.boundary.patient_data_allowed is False
    assert bundle.boundary.live_network_default_enabled is False
    assert bundle.boundary.automatic_clinical_approval_allowed is False
    assert bundle.boundary.synthetic_approvals_count_toward_release is False
    assert len(bundle.source_policies) == 8
    assert len(bundle.coverage_profiles) == 5
    assert len(bundle.review_gates) == 8


def test_validation_profiles_are_exactly_the_five_frozen_synthetic_domains():
    bundle = load_builtin_ops_bundle()

    assert {item.domain for item in bundle.coverage_profiles} == {
        item.value for item in ValidationDomain
    }
    assert all(item.synthetic_fixture_only for item in bundle.coverage_profiles)
    assert all(not item.clinical_content_seeded for item in bundle.coverage_profiles)
    assert all(
        [(jurisdiction.system, jurisdiction.code) for jurisdiction in item.scope.jurisdictions]
        == [("iso3166_1", "CN")]
        for item in bundle.coverage_profiles
    )
    assert all(item.scope.languages == ("zh-CN",) for item in bundle.coverage_profiles)


def test_clinical_scope_rejects_global_jurisdiction():
    profile = load_builtin_ops_bundle().coverage_profiles[0]
    payload = profile.scope.model_dump(mode="json")
    payload["jurisdictions"] = [{"system": "global", "code": "GLOBAL"}]

    with pytest.raises(ValidationError, match="explicit product jurisdictions"):
        ClinicalContextScope.model_validate(payload)


def test_clinical_scope_forbids_implicit_or_empty_jurisdiction():
    profile = load_builtin_ops_bundle().coverage_profiles[0]
    payload = profile.scope.model_dump(mode="json")
    payload["jurisdictions"] = []

    with pytest.raises(ValidationError):
        ClinicalContextScope.model_validate(payload)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.org",
        "https://user:secret@example.org",
        "https://example.org/path",
        "https://localhost",
        "https://metadata.internal",
        "https://127.0.0.1",
        "https://example.org:8443",
    ],
)
def test_source_policy_rejects_unsafe_origins(origin):
    with pytest.raises(ValidationError):
        SourcePolicy.model_validate(_policy_payload(allowed_origins=[origin]))


def test_source_policy_missing_operation_is_explicit_default_deny():
    policy = SourcePolicy.model_validate(_policy_payload())

    assert policy.decision_for(SourceOperation.REGISTER_LINK_METADATA) == "allow"
    assert policy.decision_for(SourceOperation.VECTOR_INDEX) == "deny"


def test_only_metadata_operations_can_be_automatically_allowed():
    payload = _policy_payload(
        operation_rules=[
            {
                "operation": "persist_snapshot",
                "decision": "allow",
                "rationale": "Unsafe synthetic test.",
            }
        ]
    )

    with pytest.raises(ValidationError, match="may be automatic"):
        SourcePolicy.model_validate(payload)


def test_unverified_policy_cannot_allow_high_risk_reuse_even_for_fixture():
    payload = _policy_payload(
        operation_rules=[
            {
                "operation": "translate",
                "decision": "offline_fixture_only",
                "rationale": "Unsafe synthetic test.",
            }
        ]
    )

    with pytest.raises(ValidationError, match="cannot allow high-risk reuse"):
        SourcePolicy.model_validate(payload)


def test_builtin_policies_keep_live_network_and_unsafe_automation_disabled():
    bundle = load_builtin_ops_bundle()

    assert all(policy.live_network_enabled is False for policy in bundle.source_policies)
    for policy in bundle.source_policies:
        assert policy.decision_for(SourceOperation.MODEL_TRAINING) == "deny"
        assert policy.decision_for(SourceOperation.VECTOR_INDEX) == "deny"
        assert policy.decision_for(SourceOperation.PERSIST_FULL_TEXT) != "allow"
        assert policy.decision_for(SourceOperation.TRANSLATE) != "allow"


def test_every_governance_gate_is_manual_and_synthetic_never_counts():
    bundle = load_builtin_ops_bundle()

    assert {item.gate for item in bundle.review_gates} == {
        item.value for item in GovernanceGate
    }
    assert all(not item.automatic_approval_allowed for item in bundle.review_gates)
    assert all(item.synthetic_events_allowed_for_tests for item in bundle.review_gates)
    assert all(
        not item.synthetic_events_count_toward_release for item in bundle.review_gates
    )


def test_incremental_read_model_has_no_release_or_runtime_authority():
    model = load_builtin_ops_read_model()

    assert model.contract_version == "2.0.0"
    assert model.operational_state == "readiness_only"
    assert model.production_releases == ()
    assert model.boundary.knowledge_effect == "informational_only"
    assert model.boundary.runtime_authority == "none"
    invalid = model.model_dump(mode="json")
    invalid["boundary"]["runtime_authority"] = "clinical"
    with pytest.raises(ValidationError):
        model.__class__.model_validate(invalid)


def test_v2_package_does_not_change_the_v1_public_read_api():
    assert "load_builtin_ops_bundle" not in knowledge_v1.__all__
    registry = knowledge_v1.load_builtin_bundle()

    assert registry.sources
    assert registry.claims


def test_manifest_loader_rejects_byte_tampering(tmp_path):
    root = _copy_bundle(tmp_path)
    target = root / "safety_boundary_v2.json"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(KnowledgeOpsManifestError, match="size mismatch"):
        load_ops_bundle(DirectoryBundleSource(root))


def test_manifest_loader_rejects_hash_tampering_with_same_size(tmp_path):
    root = _copy_bundle(tmp_path)
    target = root / "safety_boundary_v2.json"
    payload = target.read_bytes()
    replacement = payload.replace(b'"launch_jurisdiction": "CN"', b'"launch_jurisdiction": "US"')
    assert len(replacement) == len(payload)
    target.write_bytes(replacement)

    with pytest.raises(KnowledgeOpsManifestError, match="SHA-256 mismatch"):
        load_ops_bundle(DirectoryBundleSource(root))


def test_manifest_index_pins_exact_current_heads_and_bytes():
    root = _manifest_root()
    bundle = load_builtin_ops_bundle()

    for pinned in bundle.index.files:
        payload = (root / pinned.relative_path).read_bytes()
        assert len(payload) == pinned.size
        assert hashlib.sha256(payload).hexdigest() == pinned.manifest_sha256
        assert bundle.manifest_digests[pinned.ref.key()] == pinned.manifest_sha256


def test_directory_bundle_source_rejects_traversal(tmp_path):
    root = _copy_bundle(tmp_path)
    source = DirectoryBundleSource(root)

    with pytest.raises(ValueError, match="path"):
        source.read_bytes("../bundle_index_v2.json")


def test_directory_bundle_source_rejects_symlink(tmp_path):
    root = _copy_bundle(tmp_path)
    target = root / "safety_boundary_v2.json"
    real = root / "real.json"
    target.rename(real)
    target.symlink_to(real)

    with pytest.raises(KnowledgeOpsManifestError, match="symlink"):
        load_ops_bundle(DirectoryBundleSource(root))


def test_append_only_ledger_builds_a_contiguous_hash_chain(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    first = ledger.append(
        LedgerCollection.CANDIDATE,
        "candidate-1",
        payload_type="source_candidate",
        payload={"title": "Synthetic v1"},
        recorded_by="system:test",
        synthetic=True,
    )
    second = ledger.append(
        LedgerCollection.CANDIDATE,
        "candidate-1",
        payload_type="source_candidate",
        payload={"title": "Synthetic v2"},
        recorded_by="system:test",
        synthetic=True,
    )

    assert first.record_version == 1
    assert first.supersedes_entry_sha256 is None
    assert second.record_version == 2
    assert second.supersedes_entry_sha256 == first.entry_sha256
    assert ledger.history(LedgerCollection.CANDIDATE, "candidate-1") == (first, second)
    assert ledger.head(LedgerCollection.CANDIDATE, "candidate-1") == second
    assert ledger.verify_all() == 2


def test_append_only_ledger_detects_tampering_before_next_append(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    ledger.append(
        LedgerCollection.SOURCE,
        "source-1",
        payload_type="source_record",
        payload={"title": "Synthetic"},
        recorded_by="system:test",
        synthetic=True,
    )
    path = ledger.root / "records" / "source" / "source-1" / "00000001.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["title"] = "Tampered"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(KnowledgeOpsIntegrityError, match="SHA-256 mismatch"):
        ledger.history(LedgerCollection.SOURCE, "source-1")
    with pytest.raises(KnowledgeOpsIntegrityError, match="SHA-256 mismatch"):
        ledger.append(
            LedgerCollection.SOURCE,
            "source-1",
            payload_type="source_record",
            payload={"title": "Synthetic successor"},
            recorded_by="system:test",
            synthetic=True,
        )


@pytest.mark.parametrize("record_id", ["../escape", "a/b", "", ".hidden", "é"])
def test_append_only_ledger_rejects_unsafe_identifiers(tmp_path, record_id):
    ledger = AppendOnlyLedger(tmp_path / "ledger")

    with pytest.raises(ValueError, match="safe ledger identifier"):
        ledger.append(
            LedgerCollection.GAP,
            record_id,
            payload_type="coverage_gap",
            payload={"reason": "Synthetic"},
            recorded_by="system:test",
            synthetic=True,
        )


def test_append_only_ledger_rejects_non_json_values(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")

    with pytest.raises(ValueError, match="canonical JSON"):
        ledger.append(
            LedgerCollection.CANDIDATE,
            "candidate-1",
            payload_type="source_candidate",
            payload={"invalid": float("nan")},
            recorded_by="system:test",
            synthetic=True,
        )


def test_append_only_ledger_requires_timezone_aware_timestamp(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")

    with pytest.raises(ValueError, match="timezone"):
        ledger.append(
            LedgerCollection.CANDIDATE,
            "candidate-1",
            payload_type="source_candidate",
            payload={"title": "Synthetic"},
            recorded_by="system:test",
            recorded_at=datetime(2026, 8, 14),
            synthetic=True,
        )


def test_append_only_ledger_serializes_concurrent_successors(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")

    def append(index: int):
        return ledger.append(
            LedgerCollection.CHANGE_SET,
            "change-1",
            payload_type="change_set",
            payload={"sequence": index},
            recorded_by="system:test",
            recorded_at=datetime.now(timezone.utc),
            synthetic=True,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(append, range(12)))

    assert {item.record_version for item in entries} == set(range(1, 13))
    history = ledger.history(LedgerCollection.CHANGE_SET, "change-1")
    assert len(history) == 12
    assert ledger.verify_all() == 12


def test_ledger_rejects_record_directory_symlink(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    outside = tmp_path / "outside"
    outside.mkdir()
    record_parent = ledger.root / "records" / "gap"
    record_parent.mkdir(parents=True)
    (record_parent / "gap-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(KnowledgeOpsIntegrityError, match="symlink"):
        ledger.append(
            LedgerCollection.GAP,
            "gap-1",
            payload_type="coverage_gap",
            payload={"reason": "Synthetic"},
            recorded_by="system:test",
            synthetic=True,
        )
