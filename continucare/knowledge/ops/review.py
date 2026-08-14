"""Reviewer identities, immutable Review Packets, and append-only events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, ValidationError, model_validator

from continucare.knowledge.ops.acquisition import (
    ChangeSet,
    KnowledgeGap,
    SourceCandidate,
    SourcePolicyRef,
    SourceSnapshot,
)
from continucare.knowledge.ops.manifests import KnowledgeOpsBundle
from continucare.knowledge.ops.models import (
    ClinicalContextScope,
    GovernanceManifestEvidence,
    GovernanceGate,
    KnowledgeOpsIntegrityError,
    KnowledgeOpsPolicyError,
    NonBlank,
    ReviewerRole,
    SafeId,
    SourceOperation,
    StrictModel,
)
from continucare.knowledge.ops.promotion import GovernedSourceV2, PromotionDecision
from continucare.knowledge.ops.security import assert_no_sensitive_data
from continucare.knowledge.ops.store import (
    AppendOnlyLedger,
    LedgerCollection,
    LedgerRef,
)


class ReviewerAssurance(StrEnum):
    SYNTHETIC_TEST = "synthetic_test"
    IDENTITY_UNVERIFIED = "identity_unverified"
    FORMALLY_VERIFIED = "formally_verified"


class ReviewerIdentity(StrictModel):
    identity_id: SafeId
    display_name: NonBlank
    roles: tuple[ReviewerRole, ...] = Field(min_length=1)
    assurance: ReviewerAssurance
    synthetic: bool
    verification_reference: NonBlank | None = None
    verification_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    verified_by: NonBlank | None = None
    verified_at: datetime | None = None
    active: bool = True

    @model_validator(mode="after")
    def validate_identity(self) -> "ReviewerIdentity":
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("reviewer roles must be unique")
        if self.assurance == ReviewerAssurance.SYNTHETIC_TEST:
            if not self.synthetic:
                raise ValueError("synthetic reviewer assurance requires synthetic=true")
            if any(
                item is not None
                for item in (
                    self.verification_reference,
                    self.verification_evidence_sha256,
                    self.verified_by,
                    self.verified_at,
                )
            ):
                raise ValueError("synthetic reviewer cannot claim formal verification")
        elif self.assurance == ReviewerAssurance.IDENTITY_UNVERIFIED:
            if self.synthetic:
                raise ValueError("unverified real identity cannot be marked synthetic")
            if any(
                item is not None
                for item in (
                    self.verification_reference,
                    self.verification_evidence_sha256,
                    self.verified_by,
                    self.verified_at,
                )
            ):
                raise ValueError("unverified identity cannot carry verification evidence")
        else:
            if self.synthetic:
                raise ValueError("formally verified reviewer cannot be synthetic")
            if any(
                item is None
                for item in (
                    self.verification_reference,
                    self.verification_evidence_sha256,
                    self.verified_by,
                    self.verified_at,
                )
            ):
                raise ValueError("formal reviewer requires verification evidence and time")
            if self.verified_at.tzinfo is None:
                raise ValueError("verified_at must include a timezone")
        return self

    @property
    def production_eligible(self) -> bool:
        return (
            self.active
            and self.assurance == ReviewerAssurance.FORMALLY_VERIFIED
            and not self.synthetic
        )


class ReviewerDirectory(Protocol):
    def resolve(self, identity_id: str) -> ReviewerIdentity | None: ...


class InMemoryReviewerDirectory:
    """Test/readiness directory; formal identities are rejected by default."""

    def __init__(
        self,
        reviewers: tuple[ReviewerIdentity, ...] = (),
    ) -> None:
        identities = [item.identity_id for item in reviewers]
        if len(identities) != len(set(identities)):
            raise ValueError("reviewer identities must be unique")
        if any(
            item.assurance == ReviewerAssurance.FORMALLY_VERIFIED
            for item in reviewers
        ):
            raise KnowledgeOpsPolicyError(
                "in-memory reviewer directory cannot assert formal production identity"
            )
        self._reviewers = {item.identity_id: item for item in reviewers}

    def resolve(self, identity_id: str) -> ReviewerIdentity | None:
        return self._reviewers.get(identity_id)


class ReviewSubjectKind(StrEnum):
    SOURCE_CANDIDATE = "source_candidate"
    SOURCE_SNAPSHOT = "source_snapshot"
    SOURCE = "source"
    CHANGE_SET = "change_set"
    CLINICAL_CLAIM = "clinical_claim"
    BINDING = "binding"
    TERMINOLOGY_MAPPING = "terminology_mapping"
    TRANSLATION = "translation"
    PATIENT_CONTENT = "patient_content"
    KNOWLEDGE_RELEASE = "knowledge_release"


_SUBJECT_COLLECTIONS = {
    ReviewSubjectKind.SOURCE_CANDIDATE: LedgerCollection.CANDIDATE,
    ReviewSubjectKind.SOURCE_SNAPSHOT: LedgerCollection.SNAPSHOT,
    ReviewSubjectKind.SOURCE: LedgerCollection.SOURCE,
    ReviewSubjectKind.CHANGE_SET: LedgerCollection.CHANGE_SET,
    ReviewSubjectKind.CLINICAL_CLAIM: LedgerCollection.CLAIM,
    ReviewSubjectKind.BINDING: LedgerCollection.BINDING,
    ReviewSubjectKind.TERMINOLOGY_MAPPING: LedgerCollection.TERMINOLOGY_MAPPING,
    ReviewSubjectKind.TRANSLATION: LedgerCollection.TRANSLATION,
    ReviewSubjectKind.PATIENT_CONTENT: LedgerCollection.PATIENT_CONTENT,
    ReviewSubjectKind.KNOWLEDGE_RELEASE: LedgerCollection.RELEASE_CANDIDATE,
}

_SUBJECT_GATES = {
    ReviewSubjectKind.SOURCE_CANDIDATE: GovernanceGate.SOURCE_PROMOTION,
    ReviewSubjectKind.SOURCE_SNAPSHOT: GovernanceGate.CONTENT_PERSISTENCE,
    ReviewSubjectKind.SOURCE: GovernanceGate.SOURCE_PROMOTION,
    ReviewSubjectKind.CHANGE_SET: GovernanceGate.CONTENT_PERSISTENCE,
    ReviewSubjectKind.CLINICAL_CLAIM: GovernanceGate.CLINICAL_CLAIM_APPROVAL,
    ReviewSubjectKind.BINDING: GovernanceGate.BINDING_APPROVAL,
    ReviewSubjectKind.TERMINOLOGY_MAPPING: GovernanceGate.TERMINOLOGY_MAPPING_PROMOTION,
    ReviewSubjectKind.TRANSLATION: GovernanceGate.TRANSLATION_PROMOTION,
    ReviewSubjectKind.PATIENT_CONTENT: GovernanceGate.PATIENT_CONTENT_APPROVAL,
    ReviewSubjectKind.KNOWLEDGE_RELEASE: GovernanceGate.KNOWLEDGE_RELEASE,
}


class ReviewSubject(StrictModel):
    subject_kind: ReviewSubjectKind
    object_ref: LedgerRef


class ReviewPacket(StrictModel):
    packet_id: SafeId
    subject: ReviewSubject
    subject_entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    governance_bundle_id: SafeId
    governance_bundle_version: int = Field(ge=1)
    governance_index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    governance_manifests: tuple[GovernanceManifestEvidence, ...] = Field(
        min_length=1
    )
    gate: GovernanceGate
    requested_roles: tuple[ReviewerRole, ...] = Field(min_length=1)
    requested_source_operations: tuple[SourceOperation, ...] = ()
    source_policy: SourcePolicyRef | None = None
    scope: ClinicalContextScope
    evidence_refs: tuple[LedgerRef, ...] = ()
    open_gap_refs: tuple[LedgerRef, ...] = ()
    known_limitations: tuple[NonBlank, ...] = Field(min_length=1)
    safety_boundary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    generated_by: NonBlank
    synthetic: bool
    contains_patient_data: Literal[False] = False
    knowledge_effect: Literal["informational_only"] = "informational_only"
    runtime_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_packet(self) -> "ReviewPacket":
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        if self.subject_entry_sha256 != self.subject.object_ref.entry_sha256:
            raise ValueError("Review Packet subject digest must match its exact LedgerRef")
        for values, label in (
            (self.requested_roles, "requested role"),
            (self.requested_source_operations, "requested source operation"),
            (self.evidence_refs, "evidence ref"),
            (self.open_gap_refs, "open gap ref"),
        ):
            keys = [
                item if isinstance(item, str) else json.dumps(item.model_dump(mode="json"), sort_keys=True)
                for item in values
            ]
            if len(keys) != len(set(keys)):
                raise ValueError(f"Review Packet contains duplicate {label}")
        manifest_keys = [
            (item.file_id, item.file_version) for item in self.governance_manifests
        ]
        if len(manifest_keys) != len(set(manifest_keys)):
            raise ValueError("Review Packet contains duplicate governance manifest")
        return self


class ReviewCheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class ReviewCheck(StrictModel):
    check_id: SafeId
    result: ReviewCheckResult
    evidence_refs: tuple[LedgerRef, ...] = ()
    note: NonBlank


class OperationDecision(StrEnum):
    APPROVED = "approved"
    PROHIBITED = "prohibited"
    NEEDS_VERIFICATION = "needs_verification"


class SourceOperationReview(StrictModel):
    operation: SourceOperation
    decision: OperationDecision
    conditions: tuple[NonBlank, ...] = ()


class ReviewDecisionPayload(StrictModel):
    checklist: tuple[ReviewCheck, ...] = Field(min_length=1)
    source_operation_decisions: tuple[SourceOperationReview, ...] = ()
    confirmed_scope: ClinicalContextScope | None = None
    limitations: tuple[NonBlank, ...] = Field(min_length=1)
    knowledge_effect: Literal["informational_only"] = "informational_only"
    runtime_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_payload(self) -> "ReviewDecisionPayload":
        check_ids = [item.check_id for item in self.checklist]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("review checklist IDs must be unique")
        operations = [item.operation for item in self.source_operation_decisions]
        if len(operations) != len(set(operations)):
            raise ValueError("source operation decisions must be unique")
        return self


class ReviewAxis(StrEnum):
    METADATA_QUALITY = "metadata_quality"
    RIGHTS = "rights"
    TERMINOLOGY = "terminology"
    CLINICAL = "clinical"
    PHARMACY = "pharmacy"
    CITATION_VERIFICATION = "citation_verification"
    INTERNAL_CONSISTENCY = "internal_consistency"
    APPLICABILITY = "applicability"
    RELEASE = "release"


_ROLE_AXES = {
    ReviewerRole.KNOWLEDGE_CURATOR: {
        ReviewAxis.METADATA_QUALITY,
        ReviewAxis.CITATION_VERIFICATION,
        ReviewAxis.INTERNAL_CONSISTENCY,
        ReviewAxis.RELEASE,
    },
    ReviewerRole.RIGHTS_OFFICER: {ReviewAxis.RIGHTS, ReviewAxis.RELEASE},
    ReviewerRole.TERMINOLOGIST: {
        ReviewAxis.TERMINOLOGY,
        ReviewAxis.APPLICABILITY,
    },
    ReviewerRole.CLINICAL_REVIEWER: {
        ReviewAxis.CLINICAL,
        ReviewAxis.APPLICABILITY,
        ReviewAxis.RELEASE,
    },
    ReviewerRole.PHARMACIST: {ReviewAxis.PHARMACY, ReviewAxis.APPLICABILITY},
}


class ReviewDecision(StrEnum):
    IN_REVIEW = "in_review"
    REVISION_REQUESTED = "revision_requested"
    REJECTED = "rejected"
    APPROVED = "approved"


class ReviewEvent(StrictModel):
    event_id: SafeId
    subject: ReviewSubject
    packet_ref: LedgerRef
    gate: GovernanceGate
    axis: ReviewAxis
    decision: ReviewDecision
    reviewer_identity_id: SafeId
    reviewer_role: ReviewerRole
    reviewer_assurance: ReviewerAssurance
    reviewer_verification_reference: NonBlank | None = None
    reviewer_verification_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    reviewer_verified_by: NonBlank | None = None
    reviewer_verified_at: datetime | None = None
    decision_payload: ReviewDecisionPayload | None = None
    rationale: NonBlank
    decided_at: datetime
    counts_toward_release: bool
    synthetic: bool
    knowledge_effect: Literal["informational_only"] = "informational_only"
    runtime_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_event(self) -> "ReviewEvent":
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must include a timezone")
        if self.decision == ReviewDecision.APPROVED and self.decision_payload is None:
            raise ValueError("approved ReviewEvent requires a decision payload")
        verification_evidence = (
            self.reviewer_verification_reference,
            self.reviewer_verification_evidence_sha256,
            self.reviewer_verified_by,
            self.reviewer_verified_at,
        )
        if self.reviewer_assurance == ReviewerAssurance.FORMALLY_VERIFIED:
            if any(item is None for item in verification_evidence):
                raise ValueError("formal ReviewEvent requires identity verification evidence")
            if self.reviewer_verified_at.tzinfo is None:
                raise ValueError("reviewer verification time must include a timezone")
        elif any(item is not None for item in verification_evidence):
            raise ValueError(
                "non-formal ReviewEvent cannot carry formal identity verification evidence"
            )
        if self.counts_toward_release:
            if self.synthetic or self.decision != ReviewDecision.APPROVED:
                raise ValueError("only non-synthetic approved event can count toward release")
            if self.reviewer_assurance != ReviewerAssurance.FORMALLY_VERIFIED:
                raise ValueError("release-counting event requires formally verified reviewer")
            if (
                self.reviewer_verification_reference is None
                or self.reviewer_verification_evidence_sha256 is None
                or self.reviewer_verified_by is None
                or self.reviewer_verified_at is None
            ):
                raise ValueError("release-counting event requires identity verification evidence")
        return self


class GateDecision(StrictModel):
    subject_ref: LedgerRef
    gate: GovernanceGate
    scope: ClinicalContextScope
    approved_roles: tuple[ReviewerRole, ...]
    evidence_refs: tuple[LedgerRef, ...]
    blocking_gap_refs: tuple[LedgerRef, ...] = ()
    synthetic: bool
    production_eligible: bool


class ReviewPacketBuilder:
    def __init__(
        self, *, bundle: KnowledgeOpsBundle, ledger: AppendOnlyLedger
    ) -> None:
        self._bundle = bundle
        self._ledger = ledger

    def build(
        self,
        *,
        subject_kind: ReviewSubjectKind,
        subject_ref: LedgerRef,
        gate: GovernanceGate,
        scope: ClinicalContextScope,
        generated_by: str,
        known_limitations: tuple[str, ...],
        requested_source_operations: tuple[SourceOperation, ...] = (),
        source_policy: SourcePolicyRef | None = None,
        additional_required_roles: tuple[ReviewerRole, ...] = (),
        evidence_refs: tuple[LedgerRef, ...] = (),
        open_gap_refs: tuple[LedgerRef, ...] = (),
        generated_at: datetime | None = None,
    ) -> LedgerRef:
        subject_kind_value = ReviewSubjectKind(subject_kind)
        expected_collection = _SUBJECT_COLLECTIONS[subject_kind_value]
        expected_gate = _SUBJECT_GATES.get(subject_kind_value)
        if expected_gate is None or expected_gate != GovernanceGate(gate):
            raise KnowledgeOpsPolicyError("Review subject is incompatible with gate")
        subject_entry = self._ledger.get(subject_ref)
        if subject_entry.collection != expected_collection.value:
            raise KnowledgeOpsPolicyError("Review subject collection is incompatible")
        if self._ledger.head(subject_ref.collection, subject_ref.record_id).ref != subject_ref:
            raise KnowledgeOpsPolicyError("Review Packet cannot target a stale subject")
        assert_no_sensitive_data(subject_entry.payload)
        assert_no_sensitive_data(
            {
                "known_limitations": known_limitations,
                "generated_by": generated_by,
            }
        )
        gate_policy = self._bundle.review_gate(gate)
        requested_roles = tuple(
            dict.fromkeys((*gate_policy.required_roles, *additional_required_roles))
        )
        source_policy_ref = source_policy
        operations = tuple(
            dict.fromkeys(
                (
                    *_default_operations_for_gate(gate),
                    *requested_source_operations,
                )
            )
        )
        owning_candidate_ref = _owning_candidate_ref(
            subject_kind=subject_kind_value,
            subject_ref=subject_ref,
            subject_payload=subject_entry.payload,
        )
        if owning_candidate_ref is not None:
            candidate_entry = self._ledger.get(owning_candidate_ref)
            if candidate_entry.collection != LedgerCollection.CANDIDATE.value:
                raise KnowledgeOpsPolicyError(
                    "Review subject does not reference a SourceCandidate"
                )
            candidate = SourceCandidate.model_validate(candidate_entry.payload)
            profile = next(
                (
                    item
                    for item in self._bundle.coverage_profiles
                    if item.profile_id == candidate.validation_profile_id
                ),
                None,
            )
            if profile is None:
                raise KnowledgeOpsPolicyError(
                    "Review subject references an unknown validation profile"
                )
            if scope != profile.scope:
                raise KnowledgeOpsPolicyError("Review Packet scope differs from candidate profile")
            source_policy_ref = candidate.policy
            policy = self._bundle.source_policy(
                candidate.policy.policy_id, candidate.policy.policy_version
            )
            if policy.status != "active":
                raise KnowledgeOpsPolicyError("Review Packet cannot use retired SourcePolicy")
            for operation in operations:
                if policy.decision_for(operation) == "deny":
                    raise KnowledgeOpsPolicyError(
                        f"SourcePolicy denies requested review operation {operation}"
                    )
        elif operations:
            if source_policy_ref is None:
                raise KnowledgeOpsPolicyError(
                    "source operations require an exact SourcePolicyRef"
                )
            policy = self._bundle.source_policy(
                source_policy_ref.policy_id, source_policy_ref.policy_version
            )
            if policy.status != "active":
                raise KnowledgeOpsPolicyError("Review Packet cannot use retired SourcePolicy")
            for operation in operations:
                if policy.decision_for(operation) == "deny":
                    raise KnowledgeOpsPolicyError(
                        f"SourcePolicy denies requested review operation {operation}"
                    )

        discovered_gap_refs = _discover_open_related_gap_refs(
            ledger=self._ledger,
            subject_kind=subject_kind_value,
            subject_ref=subject_ref,
            subject_payload=subject_entry.payload,
        )
        effective_open_gap_refs = _deduplicate_refs(
            (*open_gap_refs, *discovered_gap_refs)
        )

        synthetic = subject_entry.synthetic
        for reference in (*evidence_refs, *effective_open_gap_refs):
            entry = self._ledger.get(reference)
            assert_no_sensitive_data(entry.payload)
            synthetic = synthetic or entry.synthetic
        for reference in effective_open_gap_refs:
            entry = self._ledger.get(reference)
            if entry.collection != LedgerCollection.GAP.value:
                raise KnowledgeOpsPolicyError("open_gap_refs must reference KnowledgeGap")
            gap = KnowledgeGap.model_validate(entry.payload)
            if gap.lifecycle != "open":
                raise KnowledgeOpsPolicyError("Review Packet gap must be open")

        timestamp = generated_at or datetime.now(timezone.utc)
        subject = ReviewSubject(subject_kind=subject_kind, object_ref=subject_ref)
        packet_id = _chain_id("packet", subject_ref, str(gate))
        packet = ReviewPacket(
            packet_id=packet_id,
            subject=subject,
            subject_entry_sha256=subject_ref.entry_sha256,
            governance_bundle_id=self._bundle.index.bundle_id,
            governance_bundle_version=self._bundle.index.bundle_version,
            governance_index_sha256=self._bundle.index_sha256(),
            governance_manifests=self._bundle.manifest_evidence(),
            gate=gate,
            requested_roles=requested_roles,
            requested_source_operations=operations,
            source_policy=source_policy_ref,
            scope=scope,
            evidence_refs=evidence_refs,
            open_gap_refs=effective_open_gap_refs,
            known_limitations=known_limitations,
            safety_boundary_sha256=_boundary_digest(self._bundle),
            generated_at=timestamp,
            generated_by=generated_by,
            synthetic=synthetic,
        )
        return self._ledger.append(
            LedgerCollection.REVIEW_PACKET,
            packet_id,
            payload_type="review_packet",
            payload=packet,
            recorded_by=generated_by,
            recorded_at=timestamp,
            synthetic=synthetic,
        ).ref


class ReviewEventService:
    def __init__(
        self,
        *,
        bundle: KnowledgeOpsBundle,
        ledger: AppendOnlyLedger,
        reviewers: ReviewerDirectory,
    ) -> None:
        self._bundle = bundle
        self._ledger = ledger
        self._reviewers = reviewers

    def record(
        self,
        *,
        packet_ref: LedgerRef,
        reviewer_identity_id: str,
        reviewer_role: ReviewerRole,
        axis: ReviewAxis,
        decision: ReviewDecision,
        rationale: str,
        decision_payload: ReviewDecisionPayload | None = None,
        decided_at: datetime | None = None,
    ) -> LedgerRef:
        packet_entry = self._ledger.get(packet_ref)
        if packet_entry.collection != LedgerCollection.REVIEW_PACKET.value:
            raise KnowledgeOpsPolicyError("ReviewEvent requires a Review Packet")
        if self._ledger.head(packet_ref.collection, packet_ref.record_id).ref != packet_ref:
            raise KnowledgeOpsPolicyError("ReviewEvent cannot use a stale Review Packet")
        packet = ReviewPacket.model_validate(packet_entry.payload)
        subject_entry, packet_material_entries = _resolve_packet_material(
            bundle=self._bundle,
            ledger=self._ledger,
            packet=packet,
        )
        assert_no_sensitive_data(packet_entry.payload)
        identity = self._reviewers.resolve(reviewer_identity_id)
        if (
            identity is None
            or identity.identity_id != reviewer_identity_id
            or not identity.active
        ):
            raise KnowledgeOpsPolicyError("reviewer identity cannot be resolved as active")
        role = ReviewerRole(reviewer_role)
        if role not in identity.roles or role not in packet.requested_roles:
            raise KnowledgeOpsPolicyError("reviewer role is not authorized for this packet")
        axis_value = ReviewAxis(axis)
        if axis_value not in _ROLE_AXES[role]:
            raise KnowledgeOpsPolicyError("review axis is incompatible with reviewer role")
        decision_value = ReviewDecision(decision)
        if (
            decision_value == ReviewDecision.APPROVED
            and identity.assurance == ReviewerAssurance.IDENTITY_UNVERIFIED
        ):
            raise KnowledgeOpsPolicyError("unverified reviewer cannot approve")
        if decision_value == ReviewDecision.APPROVED:
            if decision_payload is None:
                raise KnowledgeOpsPolicyError("approved review requires decision payload")
            _validate_approved_payload(packet, role, decision_payload)

        supplemental_evidence_synthetic = False
        if decision_payload is not None:
            assert_no_sensitive_data(
                {
                    "decision_payload": decision_payload.model_dump(mode="json"),
                    "rationale": rationale,
                }
            )
            for check in decision_payload.checklist:
                for evidence_ref in check.evidence_refs:
                    evidence_entry = self._ledger.get(evidence_ref)
                    assert_no_sensitive_data(evidence_entry.payload)
                    supplemental_evidence_synthetic = (
                        supplemental_evidence_synthetic or evidence_entry.synthetic
                    )
        else:
            assert_no_sensitive_data({"rationale": rationale})

        gate_policy = self._bundle.review_gate(packet.gate)
        if identity.synthetic:
            if not gate_policy.synthetic_events_allowed_for_tests:
                raise KnowledgeOpsPolicyError("gate does not allow synthetic test events")
            if not (
                packet_entry.synthetic
                or packet.synthetic
                or subject_entry.synthetic
                or any(entry.synthetic for entry in packet_material_entries)
            ):
                raise KnowledgeOpsPolicyError(
                    "synthetic reviewer cannot approve or annotate non-synthetic subject"
                )

        timestamp = decided_at or datetime.now(timezone.utc)
        synthetic = (
            packet_entry.synthetic
            or packet.synthetic
            or subject_entry.synthetic
            or any(entry.synthetic for entry in packet_material_entries)
            or identity.synthetic
            or supplemental_evidence_synthetic
        )
        counts = (
            decision_value == ReviewDecision.APPROVED
            and identity.production_eligible
            and not synthetic
        )
        event_record_id = _chain_id("review", packet.subject.object_ref, role.value)
        event = ReviewEvent(
            event_id=_event_id(event_record_id, timestamp),
            subject=packet.subject,
            packet_ref=packet_ref,
            gate=packet.gate,
            axis=axis_value,
            decision=decision_value,
            reviewer_identity_id=identity.identity_id,
            reviewer_role=role,
            reviewer_assurance=identity.assurance,
            reviewer_verification_reference=identity.verification_reference,
            reviewer_verification_evidence_sha256=(
                identity.verification_evidence_sha256
            ),
            reviewer_verified_by=identity.verified_by,
            reviewer_verified_at=identity.verified_at,
            decision_payload=decision_payload,
            rationale=rationale,
            decided_at=timestamp,
            counts_toward_release=counts,
            synthetic=synthetic,
        )
        return self._ledger.append(
            LedgerCollection.REVIEW_EVENT,
            event_record_id,
            payload_type="review_event_v2",
            payload=event,
            recorded_by=identity.identity_id,
            recorded_at=timestamp,
            synthetic=synthetic,
        ).ref


class ReviewLedgerDecisionProvider:
    """Resolve only the unique latest event for every role required by a gate."""

    def __init__(
        self, *, bundle: KnowledgeOpsBundle, ledger: AppendOnlyLedger
    ) -> None:
        self._bundle = bundle
        self._ledger = ledger

    def resolve_gate(
        self, subject_ref: LedgerRef, gate: GovernanceGate
    ) -> GateDecision | None:
        gate_policy = self._bundle.review_gate(gate)
        packet_id = _chain_id("packet", subject_ref, str(gate))
        packet_head = self._ledger.head(LedgerCollection.REVIEW_PACKET, packet_id)
        if packet_head is None:
            return None
        try:
            packet = ReviewPacket.model_validate(packet_head.payload)
        except ValidationError:
            return None
        if packet.subject.object_ref != subject_ref or packet.gate != str(gate):
            return None
        if not set(gate_policy.required_roles).issubset(packet.requested_roles):
            return None
        try:
            subject_head, packet_material_entries = _resolve_packet_material(
                bundle=self._bundle,
                ledger=self._ledger,
                packet=packet,
            )
            assert_no_sensitive_data(packet_head.payload)
        except (
            KeyError,
            KnowledgeOpsIntegrityError,
            KnowledgeOpsPolicyError,
            ValidationError,
            ValueError,
        ):
            return None
        current_related_gaps = _discover_open_related_gap_refs(
            ledger=self._ledger,
            subject_kind=packet.subject.subject_kind,
            subject_ref=subject_ref,
            subject_payload=subject_head.payload,
        )
        packet_gap_keys = {_ref_key(reference) for reference in packet.open_gap_refs}
        if any(
            _ref_key(reference) not in packet_gap_keys
            for reference in current_related_gaps
        ):
            return None
        for reference in packet.open_gap_refs:
            gap_head = self._ledger.head(reference.collection, reference.record_id)
            if gap_head is None or gap_head.ref != reference:
                return None
        events: list[tuple[LedgerRef, ReviewEvent]] = []
        supplemental_evidence_synthetic = False
        for role in packet.requested_roles:
            record_id = _chain_id("review", subject_ref, str(role))
            head = self._ledger.head(LedgerCollection.REVIEW_EVENT, record_id)
            if head is None:
                return None
            try:
                event = ReviewEvent.model_validate(head.payload)
            except ValidationError:
                return None
            if (
                event.subject != packet.subject
                or event.gate != str(gate)
                or event.reviewer_role != str(role)
                or event.decision != ReviewDecision.APPROVED.value
                or event.axis not in _ROLE_AXES[ReviewerRole(role)]
            ):
                return None
            if event.packet_ref != packet_head.ref:
                return None
            try:
                assert_no_sensitive_data(head.payload)
                _validate_approved_payload(
                    packet,
                    ReviewerRole(role),
                    event.decision_payload,
                )
                for check in event.decision_payload.checklist:
                    for evidence_ref in check.evidence_refs:
                        evidence_entry = self._ledger.get(evidence_ref)
                        assert_no_sensitive_data(evidence_entry.payload)
                        supplemental_evidence_synthetic = (
                            supplemental_evidence_synthetic
                            or evidence_entry.synthetic
                        )
            except (
                KnowledgeOpsIntegrityError,
                KnowledgeOpsPolicyError,
                ValidationError,
                ValueError,
            ):
                return None
            events.append((head.ref, event))
        synthetic = (
            packet_head.synthetic
            or packet.synthetic
            or subject_head.synthetic
            or any(entry.synthetic for entry in packet_material_entries)
            or any(
                self._ledger.get(reference).synthetic or event.synthetic
                for reference, event in events
            )
            or supplemental_evidence_synthetic
        )
        if len({event.reviewer_identity_id for _, event in events}) != len(events):
            return None
        production_eligible = all(
            event.counts_toward_release for _, event in events
        ) and not synthetic
        return GateDecision(
            subject_ref=subject_ref,
            gate=gate,
            scope=packet.scope,
            approved_roles=tuple(packet.requested_roles),
            evidence_refs=tuple(reference for reference, _ in events),
            blocking_gap_refs=packet.open_gap_refs,
            synthetic=synthetic,
            production_eligible=production_eligible,
        )

    def decision_for(
        self, subject_ref: LedgerRef, gate: GovernanceGate
    ) -> PromotionDecision | None:
        if GovernanceGate(gate) != GovernanceGate.SOURCE_PROMOTION:
            return None
        resolved = self.resolve_gate(subject_ref, gate)
        if resolved is None:
            return None
        return PromotionDecision(
            subject_ref=resolved.subject_ref,
            approved_roles=resolved.approved_roles,
            evidence_refs=resolved.evidence_refs,
            blocking_gap_refs=resolved.blocking_gap_refs,
            synthetic=resolved.synthetic,
            production_eligible=resolved.production_eligible,
        )


def _resolve_packet_material(
    *,
    bundle: KnowledgeOpsBundle,
    ledger: AppendOnlyLedger,
    packet: ReviewPacket,
):
    if (
        packet.governance_bundle_id != bundle.index.bundle_id
        or packet.governance_bundle_version != bundle.index.bundle_version
        or packet.governance_index_sha256 != bundle.index_sha256()
        or packet.governance_manifests != bundle.manifest_evidence()
        or packet.safety_boundary_sha256 != _boundary_digest(bundle)
    ):
        raise KnowledgeOpsPolicyError(
            "Review Packet does not pin the currently loaded governance bundle"
        )

    subject_kind = ReviewSubjectKind(packet.subject.subject_kind)
    expected_gate = _SUBJECT_GATES[subject_kind]
    expected_collection = _SUBJECT_COLLECTIONS[subject_kind]
    if packet.gate != expected_gate or packet.subject.object_ref.collection != expected_collection:
        raise KnowledgeOpsPolicyError("Review Packet subject is incompatible with its gate")
    gate_policy = bundle.review_gate(packet.gate)
    if not set(gate_policy.required_roles).issubset(packet.requested_roles):
        raise KnowledgeOpsPolicyError("Review Packet omits a gate-required role")
    required_operations = set(_default_operations_for_gate(packet.gate))
    if not required_operations.issubset(packet.requested_source_operations):
        raise KnowledgeOpsPolicyError("Review Packet omits a gate-required Source operation")

    subject_entry = ledger.get(packet.subject.object_ref)
    assert_no_sensitive_data(subject_entry.payload)
    subject_head = ledger.head(
        packet.subject.object_ref.collection,
        packet.subject.object_ref.record_id,
    )
    if subject_head is None or subject_head.ref != packet.subject.object_ref:
        raise KnowledgeOpsPolicyError("Review Packet subject is not the current ledger head")

    source_policy_ref = packet.source_policy
    owning_candidate_ref = _owning_candidate_ref(
        subject_kind=subject_kind,
        subject_ref=packet.subject.object_ref,
        subject_payload=subject_entry.payload,
    )
    if owning_candidate_ref is not None:
        candidate_entry = ledger.get(owning_candidate_ref)
        if candidate_entry.collection != LedgerCollection.CANDIDATE.value:
            raise KnowledgeOpsPolicyError("Review Packet candidate reference is invalid")
        candidate_head = ledger.head(
            owning_candidate_ref.collection,
            owning_candidate_ref.record_id,
        )
        if candidate_head is None or candidate_head.ref != owning_candidate_ref:
            raise KnowledgeOpsPolicyError("Review Packet candidate is not current")
        candidate = SourceCandidate.model_validate(candidate_entry.payload)
        profile = next(
            (
                item
                for item in bundle.coverage_profiles
                if item.profile_id == candidate.validation_profile_id
            ),
            None,
        )
        if profile is None or packet.scope != profile.scope:
            raise KnowledgeOpsPolicyError(
                "Review Packet scope differs from the exact candidate profile"
            )
        if source_policy_ref != candidate.policy:
            raise KnowledgeOpsPolicyError(
                "Review Packet SourcePolicy differs from its SourceCandidate"
            )

    if packet.requested_source_operations:
        if source_policy_ref is None:
            raise KnowledgeOpsPolicyError(
                "Review Packet Source operations require an exact SourcePolicy"
            )
        policy = bundle.source_policy(
            source_policy_ref.policy_id,
            source_policy_ref.policy_version,
        )
        if policy.status != "active":
            raise KnowledgeOpsPolicyError("Review Packet uses a retired SourcePolicy")
        if any(
            policy.decision_for(operation) == "deny"
            for operation in packet.requested_source_operations
        ):
            raise KnowledgeOpsPolicyError("Review Packet requests a denied Source operation")

    material_entries = []
    for reference in (*packet.evidence_refs, *packet.open_gap_refs):
        entry = ledger.get(reference)
        assert_no_sensitive_data(entry.payload)
        material_entries.append(entry)
    for reference in packet.open_gap_refs:
        entry = ledger.get(reference)
        if entry.collection != LedgerCollection.GAP.value:
            raise KnowledgeOpsPolicyError("Review Packet open gap ref is not a KnowledgeGap")
        gap = KnowledgeGap.model_validate(entry.payload)
        gap_head = ledger.head(reference.collection, reference.record_id)
        if gap.lifecycle != "open" or gap_head is None or gap_head.ref != reference:
            raise KnowledgeOpsPolicyError("Review Packet does not pin a current open gap")

    current_related_gaps = _discover_open_related_gap_refs(
        ledger=ledger,
        subject_kind=subject_kind,
        subject_ref=packet.subject.object_ref,
        subject_payload=subject_entry.payload,
    )
    packet_gap_keys = {_ref_key(reference) for reference in packet.open_gap_refs}
    if any(_ref_key(reference) not in packet_gap_keys for reference in current_related_gaps):
        raise KnowledgeOpsPolicyError("Review Packet omits a current related open gap")
    return subject_entry, tuple(material_entries)


def _validate_approved_payload(
    packet: ReviewPacket,
    role: ReviewerRole,
    payload: ReviewDecisionPayload,
) -> None:
    results = {item.result for item in payload.checklist}
    if ReviewCheckResult.FAIL.value in results or ReviewCheckResult.PASS.value not in results:
        raise KnowledgeOpsPolicyError(
            "approved review checklist requires at least one pass and no failures"
        )
    if role == ReviewerRole.RIGHTS_OFFICER:
        requested = set(packet.requested_source_operations)
        decisions = {
            item.operation: item.decision for item in payload.source_operation_decisions
        }
        if set(decisions) != requested or any(
            decision != OperationDecision.APPROVED.value
            for decision in decisions.values()
        ):
            raise KnowledgeOpsPolicyError(
                "rights approval must explicitly approve every requested source operation"
            )
    elif payload.source_operation_decisions:
        raise KnowledgeOpsPolicyError(
            "only rights officer can decide source reuse operations"
        )
    if role in {
        ReviewerRole.CLINICAL_REVIEWER,
        ReviewerRole.TERMINOLOGIST,
        ReviewerRole.PHARMACIST,
    } and payload.confirmed_scope != packet.scope:
        raise KnowledgeOpsPolicyError("specialist approval must confirm exact packet scope")


def _default_operations_for_gate(
    gate: GovernanceGate,
) -> tuple[SourceOperation, ...]:
    defaults = {
        GovernanceGate.SOURCE_PROMOTION: (SourceOperation.REGISTER_LINK_METADATA,),
        GovernanceGate.CONTENT_PERSISTENCE: (SourceOperation.PERSIST_SNAPSHOT,),
        GovernanceGate.TERMINOLOGY_MAPPING_PROMOTION: (
            SourceOperation.CREATE_MAPPING,
        ),
        GovernanceGate.TRANSLATION_PROMOTION: (SourceOperation.TRANSLATE,),
    }
    return defaults.get(GovernanceGate(gate), ())


def _boundary_digest(bundle: KnowledgeOpsBundle) -> str:
    payload = json.dumps(
        bundle.boundary.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _related_subject_refs(
    *,
    subject_kind: ReviewSubjectKind,
    subject_ref: LedgerRef,
    subject_payload: dict[str, object],
) -> tuple[LedgerRef, ...]:
    related = [subject_ref]
    owning_candidate_ref = _owning_candidate_ref(
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        subject_payload=subject_payload,
    )
    if owning_candidate_ref is not None:
        related.append(owning_candidate_ref)
    return _deduplicate_refs(tuple(related))


def _owning_candidate_ref(
    *,
    subject_kind: ReviewSubjectKind,
    subject_ref: LedgerRef,
    subject_payload: dict[str, object],
) -> LedgerRef | None:
    if subject_kind == ReviewSubjectKind.SOURCE_CANDIDATE:
        SourceCandidate.model_validate(subject_payload)
        return subject_ref
    if subject_kind == ReviewSubjectKind.SOURCE_SNAPSHOT:
        return SourceSnapshot.model_validate(subject_payload).candidate_ref
    if subject_kind == ReviewSubjectKind.CHANGE_SET:
        return ChangeSet.model_validate(subject_payload).candidate_ref
    if subject_kind == ReviewSubjectKind.SOURCE:
        return GovernedSourceV2.model_validate(subject_payload).candidate_ref
    return None


def _discover_open_related_gap_refs(
    *,
    ledger: AppendOnlyLedger,
    subject_kind: ReviewSubjectKind,
    subject_ref: LedgerRef,
    subject_payload: dict[str, object],
) -> tuple[LedgerRef, ...]:
    related_subject_refs = _related_subject_refs(
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        subject_payload=subject_payload,
    )
    return tuple(
        entry.ref
        for entry in ledger.list_heads(LedgerCollection.GAP)
        if _is_open_related_gap(entry.payload, related_subject_refs)
    )


def _is_open_related_gap(
    payload: dict[str, object], related_subject_refs: tuple[LedgerRef, ...]
) -> bool:
    gap = KnowledgeGap.model_validate(payload)
    return (
        gap.lifecycle == "open"
        and gap.subject_ref is not None
        and _ref_key(gap.subject_ref)
        in {_ref_key(reference) for reference in related_subject_refs}
    )


def _deduplicate_refs(references: tuple[LedgerRef, ...]) -> tuple[LedgerRef, ...]:
    unique: dict[tuple[str, str, int, str], LedgerRef] = {}
    for reference in references:
        unique.setdefault(_ref_key(reference), reference)
    return tuple(unique.values())


def _ref_key(reference: LedgerRef) -> tuple[str, str, int, str]:
    return (
        str(reference.collection),
        reference.record_id,
        reference.record_version,
        reference.entry_sha256,
    )


def _chain_id(prefix: str, subject_ref: LedgerRef, discriminator: str) -> str:
    raw = (
        f"{prefix}-{subject_ref.collection}-{subject_ref.record_id}-"
        f"{subject_ref.record_version}-{subject_ref.entry_sha256[:12]}-{discriminator}"
    )
    safe = "".join(character if character.isalnum() or character in "._-" else "-" for character in raw)
    if len(safe) <= 128:
        return safe
    digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()[:20]
    return f"{safe[:107]}-{digest}"


def _event_id(record_id: str, timestamp: datetime) -> str:
    digest = hashlib.sha256(
        f"{record_id}|{timestamp.isoformat()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"event-{digest}"
