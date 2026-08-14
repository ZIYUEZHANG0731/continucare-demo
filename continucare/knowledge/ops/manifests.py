"""Hash-pinned, atomic loading for Knowledge Operations v2 manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from continucare.knowledge.ops.models import (
    CoverageProfileManifest,
    CoverageValidationProfile,
    FileRef,
    GovernanceGate,
    KnowledgeOpsBundleIndex,
    KnowledgeReleaseIntent,
    KnowledgeOpsManifestError,
    PayloadEnvelope,
    ReviewGatePolicy,
    ReviewPolicyManifest,
    ReleaseIntentManifest,
    SafetyBoundary,
    SafetyBoundaryManifest,
    SourcePolicy,
    SourcePolicyManifest,
    ValidationDomain,
    safe_relative_parts,
)


BUILTIN_INDEX_PATH = "bundle_index_v2.json"
_PAYLOAD_ADAPTER = TypeAdapter(PayloadEnvelope)


class BundleSource(Protocol):
    def read_bytes(self, relative_path: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DirectoryBundleSource:
    root: Path

    def read_bytes(self, relative_path: str) -> bytes:
        parts = safe_relative_parts(relative_path)
        root = self.root.resolve(strict=True)
        if self.root.is_symlink():
            raise ValueError("bundle root cannot be a symlink")
        current = root
        for part in parts:
            candidate = current / part
            if candidate.is_symlink():
                raise ValueError("bundle path cannot traverse a symlink")
            current = candidate
        resolved = current.resolve(strict=True)
        if root not in resolved.parents:
            raise ValueError("bundle path escaped its root")
        if not resolved.is_file():
            raise ValueError("bundle resource is not a regular file")
        return resolved.read_bytes()


@dataclass(frozen=True, slots=True)
class TraversableBundleSource:
    root: object

    def read_bytes(self, relative_path: str) -> bytes:
        parts = safe_relative_parts(relative_path)
        resource = self.root
        for part in parts:
            resource = resource.joinpath(part)  # type: ignore[attr-defined]
        if not resource.is_file():  # type: ignore[attr-defined]
            raise ValueError("bundle resource is not a file")
        payload = resource.read_bytes()  # type: ignore[attr-defined]
        if not isinstance(payload, bytes):
            raise TypeError("bundle resource must return bytes")
        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeOpsBundle:
    index: KnowledgeOpsBundleIndex
    boundary: SafetyBoundary
    source_policies: tuple[SourcePolicy, ...]
    coverage_profiles: tuple[CoverageValidationProfile, ...]
    review_gates: tuple[ReviewGatePolicy, ...]
    release_intent: KnowledgeReleaseIntent
    manifest_digests: Mapping[tuple[str, int], str]

    def source_policy(self, policy_id: str, policy_version: int = 1) -> SourcePolicy:
        for policy in self.source_policies:
            if (policy.policy_id, policy.policy_version) == (policy_id, policy_version):
                return policy
        raise KeyError(f"unknown SourcePolicy {policy_id}@{policy_version}")

    def review_gate(self, gate: GovernanceGate | str) -> ReviewGatePolicy:
        gate_value = str(gate)
        for policy in self.review_gates:
            if str(policy.gate) == gate_value:
                return policy
        raise KeyError(f"unknown review gate {gate_value}")


def load_builtin_ops_bundle() -> KnowledgeOpsBundle:
    source = TraversableBundleSource(files("continucare.knowledge.manifests_v2"))
    return load_ops_bundle(source)


def load_ops_bundle(
    source: BundleSource,
    *,
    index_path: str = BUILTIN_INDEX_PATH,
) -> KnowledgeOpsBundle:
    """Load a complete v2 governance bundle or return no partial state."""

    try:
        index_bytes = source.read_bytes(index_path)
        index = KnowledgeOpsBundleIndex.model_validate_json(index_bytes)
    except (OSError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise KnowledgeOpsManifestError(f"invalid Knowledge Ops index: {exc}") from exc

    envelopes: list[PayloadEnvelope] = []
    digests: dict[tuple[str, int], str] = {}
    for pinned in index.files:
        try:
            payload = source.read_bytes(pinned.relative_path)
        except Exception as exc:
            raise KnowledgeOpsManifestError(
                f"cannot read pinned file {pinned.relative_path}: {exc}"
            ) from exc
        if len(payload) != pinned.size:
            raise KnowledgeOpsManifestError(
                f"pinned file {pinned.relative_path} size mismatch"
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != pinned.manifest_sha256:
            raise KnowledgeOpsManifestError(
                f"pinned file {pinned.relative_path} SHA-256 mismatch"
            )
        try:
            envelope = _PAYLOAD_ADAPTER.validate_json(payload)
        except ValidationError as exc:
            raise KnowledgeOpsManifestError(
                f"invalid pinned file {pinned.relative_path}: {exc}"
            ) from exc
        if envelope.ref != pinned.ref:
            raise KnowledgeOpsManifestError(
                f"pinned ref does not match payload {pinned.relative_path}"
            )
        envelopes.append(envelope)
        digests[pinned.ref.key()] = digest

    _validate_file_versions(envelopes, index.current_file_refs)
    boundary_files = [item for item in envelopes if isinstance(item, SafetyBoundaryManifest)]
    source_files = [item for item in envelopes if isinstance(item, SourcePolicyManifest)]
    profile_files = [item for item in envelopes if isinstance(item, CoverageProfileManifest)]
    review_files = [item for item in envelopes if isinstance(item, ReviewPolicyManifest)]
    release_files = [item for item in envelopes if isinstance(item, ReleaseIntentManifest)]
    if not all(
        len(items) == 1
        for items in (
            boundary_files,
            source_files,
            profile_files,
            review_files,
            release_files,
        )
    ):
        raise KnowledgeOpsManifestError(
            "current v2 bundle requires exactly one boundary, source policy, "
            "coverage profile, review policy, and release intent file"
        )

    source_policies = source_files[0].policies
    coverage_profiles = profile_files[0].profiles
    review_gates = review_files[0].gates
    _validate_unique_versions(
        source_policies,
        lambda item: (item.policy_id, item.policy_version),
        "SourcePolicy",
    )
    _validate_unique_versions(
        coverage_profiles,
        lambda item: (item.profile_id, item.profile_version),
        "coverage profile",
    )
    _validate_unique_versions(review_gates, lambda item: str(item.gate), "review gate")
    if {str(item.domain) for item in coverage_profiles} != {
        item.value for item in ValidationDomain
    }:
        raise KnowledgeOpsManifestError(
            "coverage profiles must contain exactly the five frozen validation domains"
        )
    if {str(item.gate) for item in review_gates} != {item.value for item in GovernanceGate}:
        raise KnowledgeOpsManifestError(
            "review policy must contain every governance gate exactly once"
        )
    if not source_policies:
        raise KnowledgeOpsManifestError("source policy registry cannot be empty")

    return KnowledgeOpsBundle(
        index=index,
        boundary=boundary_files[0].boundary,
        source_policies=source_policies,
        coverage_profiles=coverage_profiles,
        review_gates=review_gates,
        release_intent=release_files[0].intent,
        manifest_digests=MappingProxyType(digests),
    )


def _validate_file_versions(
    envelopes: list[PayloadEnvelope], current_refs: tuple[FileRef, ...]
) -> None:
    by_id: dict[str, list[int]] = {}
    for item in envelopes:
        by_id.setdefault(item.file_id, []).append(item.file_version)
    for file_id, versions in by_id.items():
        ordered = sorted(versions)
        if ordered != list(range(1, ordered[-1] + 1)):
            raise KnowledgeOpsManifestError(
                f"manifest {file_id} versions must be append-only and contiguous"
            )
    current = {item.key() for item in current_refs}
    expected = {(file_id, max(versions)) for file_id, versions in by_id.items()}
    if current != expected:
        raise KnowledgeOpsManifestError("current_file_refs must select each exact head")


def _validate_unique_versions(items, key, label: str) -> None:
    keys = [key(item) for item in items]
    if len(keys) != len(set(keys)):
        raise KnowledgeOpsManifestError(f"duplicate {label} identity")
