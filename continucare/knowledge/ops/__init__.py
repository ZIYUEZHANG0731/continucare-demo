"""Governed, non-runtime knowledge acquisition and release-readiness APIs.

This package is intentionally separate from the stable v1 read registry.  Its
public boundary is informational-only, contains no patient-data input, and has
no authority over pathway or clinical runtime behavior.
"""

from continucare.knowledge.ops.acquisition import (
    AcquisitionEnvironment,
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionRun,
    AcquisitionService,
    ChangeKind,
    ChangeSet,
    GapKind,
    KnowledgeGap,
    QuarantineBlobRef,
    QuarantineBlobStore,
    SourceCandidate,
    SourcePolicyRef,
    SourceSnapshot,
)
from continucare.knowledge.ops.connectors import (
    DiscoveredResource,
    FetchedDocument,
    GuardedHttpConnector,
    NetworkAccessDisabled,
    OfflineFixtureConnector,
)

from continucare.knowledge.ops.manifests import (
    DirectoryBundleSource,
    KnowledgeOpsBundle,
    load_builtin_ops_bundle,
    load_ops_bundle,
)
from continucare.knowledge.ops.models import (
    KNOWLEDGE_OPS_CONTRACT_VERSION,
    ClinicalContextScope,
    CoverageValidationProfile,
    GovernanceGate,
    IntendedUse,
    KnowledgeLayer,
    KnowledgeOpsError,
    KnowledgeOpsIntegrityError,
    KnowledgeOpsManifestError,
    KnowledgeOpsPolicyError,
    LicensePosture,
    PolicyDecision,
    ReviewerRole,
    SafetyBoundary,
    SourceOperation,
    SourcePolicy,
    ValidationDomain,
)
from continucare.knowledge.ops.read_model import (
    KnowledgeOpsReadModel,
    build_ops_read_model,
    load_builtin_ops_read_model,
)
from continucare.knowledge.ops.promotion import (
    GovernedSourceV2,
    PromotionDecision,
    PromotionDecisionProvider,
    SourcePromotionService,
)
from continucare.knowledge.ops.security import (
    assert_deidentified_query_terms,
    assert_no_sensitive_data,
    validate_public_peer_ip,
    validate_transport_route,
    validate_url_against_policy,
)
from continucare.knowledge.ops.store import (
    AppendOnlyLedger,
    LedgerCollection,
    LedgerEntry,
    LedgerRef,
)

__all__ = [
    "AppendOnlyLedger",
    "AcquisitionEnvironment",
    "AcquisitionRequest",
    "AcquisitionResult",
    "AcquisitionRun",
    "AcquisitionService",
    "ChangeKind",
    "ChangeSet",
    "ClinicalContextScope",
    "CoverageValidationProfile",
    "DirectoryBundleSource",
    "GovernanceGate",
    "GovernedSourceV2",
    "GuardedHttpConnector",
    "IntendedUse",
    "KNOWLEDGE_OPS_CONTRACT_VERSION",
    "KnowledgeLayer",
    "KnowledgeOpsBundle",
    "KnowledgeOpsError",
    "KnowledgeOpsIntegrityError",
    "KnowledgeOpsManifestError",
    "KnowledgeOpsPolicyError",
    "KnowledgeOpsReadModel",
    "KnowledgeGap",
    "GapKind",
    "LedgerCollection",
    "LedgerEntry",
    "LedgerRef",
    "LicensePosture",
    "PolicyDecision",
    "PromotionDecision",
    "PromotionDecisionProvider",
    "QuarantineBlobRef",
    "QuarantineBlobStore",
    "ReviewerRole",
    "SafetyBoundary",
    "SourceOperation",
    "SourceCandidate",
    "SourcePolicy",
    "SourcePolicyRef",
    "SourcePromotionService",
    "SourceSnapshot",
    "ValidationDomain",
    "DiscoveredResource",
    "FetchedDocument",
    "NetworkAccessDisabled",
    "OfflineFixtureConnector",
    "assert_deidentified_query_terms",
    "assert_no_sensitive_data",
    "build_ops_read_model",
    "load_builtin_ops_bundle",
    "load_builtin_ops_read_model",
    "load_ops_bundle",
    "validate_public_peer_ip",
    "validate_transport_route",
    "validate_url_against_policy",
]
