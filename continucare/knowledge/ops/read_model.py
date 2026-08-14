"""Incremental, UI-independent v2 read model for knowledge operations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from continucare.knowledge.ops.manifests import KnowledgeOpsBundle, load_builtin_ops_bundle
from continucare.knowledge.ops.models import (
    KNOWLEDGE_OPS_CONTRACT_VERSION,
    CoverageValidationProfile,
    ReviewGatePolicy,
    SafetyBoundary,
    SourcePolicy,
    StrictModel,
)


class KnowledgeOpsReadModel(StrictModel):
    contract_version: Literal[KNOWLEDGE_OPS_CONTRACT_VERSION]
    bundle_id: str
    bundle_version: int = Field(ge=1)
    boundary: SafetyBoundary
    source_policies: tuple[SourcePolicy, ...]
    validation_profiles: tuple[CoverageValidationProfile, ...]
    review_gates: tuple[ReviewGatePolicy, ...]
    operational_state: str = "readiness_only"
    production_releases: tuple[str, ...] = ()


def build_ops_read_model(bundle: KnowledgeOpsBundle) -> KnowledgeOpsReadModel:
    return KnowledgeOpsReadModel(
        contract_version=KNOWLEDGE_OPS_CONTRACT_VERSION,
        bundle_id=bundle.index.bundle_id,
        bundle_version=bundle.index.bundle_version,
        boundary=bundle.boundary,
        source_policies=bundle.source_policies,
        validation_profiles=bundle.coverage_profiles,
        review_gates=bundle.review_gates,
    )


def load_builtin_ops_read_model() -> KnowledgeOpsReadModel:
    return build_ops_read_model(load_builtin_ops_bundle())
