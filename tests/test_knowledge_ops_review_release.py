from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from continucare.knowledge.ops import (
    AcquisitionRequest,
    AcquisitionService,
    AppendOnlyLedger,
    GovernanceGate,
    GovernedSourceV2,
    InMemoryReviewerDirectory,
    KnowledgeGap,
    KnowledgeOpsPolicyError,
    KnowledgeReleaseBlocked,
    KnowledgeReleaseCandidate,
    LedgerCollection,
    OfflineFixtureConnector,
    QuarantineBlobStore,
    ReadinessBlockerCode,
    ReleaseArtifact,
    ReleaseReadinessReport,
    ReleaseReadinessService,
    ReviewCheck,
    ReviewDecisionPayload,
    ReviewEvent,
    ReviewEventService,
    ReviewLedgerDecisionProvider,
    ReviewPacket,
    ReviewPacketBuilder,
    ReviewerIdentity,
    ReviewSubjectKind,
    SourceOperationReview,
    SourcePromotionService,
    load_builtin_ops_bundle,
    load_builtin_ops_read_model,
)


FIXTURE_CATALOG_SHA256 = (
    "e711994018bb783236e050d890783502eaee91e7345e4d4e9808fdf542764a3f"
)


def _fixture_root() -> Path:
    return Path(__file__).parent / "fixtures" / "knowledge_ops"


def _synthetic_reviewer(role: str) -> ReviewerIdentity:
    return ReviewerIdentity(
        identity_id=f"synthetic-{role}",
        display_name=f"Synthetic {role} fixture",
        roles=(role,),
        assurance="synthetic_test",
        synthetic=True,
    )


def _reviewers(*roles: str) -> InMemoryReviewerDirectory:
    return InMemoryReviewerDirectory(tuple(_synthetic_reviewer(role) for role in roles))


def _manifest_evidence(bundle):
    return tuple(
        {
            "file_id": item.ref.file_id,
            "file_version": item.ref.file_version,
            "manifest_sha256": item.manifest_sha256,
        }
        for item in bundle.index.files
    )


def _acquisition_context(tmp_path: Path):
    bundle = load_builtin_ops_bundle()
    profile = next(
        item
        for item in bundle.coverage_profiles
        if item.profile_id == "fixture-medication-followup"
    )
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    quarantine = QuarantineBlobStore(tmp_path / "quarantine")
    connector = OfflineFixtureConnector(
        _fixture_root(), catalog_sha256=FIXTURE_CATALOG_SHA256
    )
    request = AcquisitionRequest(
        request_id="review-readiness",
        validation_profile_id=profile.profile_id,
        trigger="scheduled",
        policy_ids=("nmpa-cn-regulatory-metadata",),
        topic_codes=(
            {
                "system": "urn:continucare:synthetic-topic",
                "version": "1",
                "code": "review-readiness",
            },
        ),
        query_terms=("medication followup",),
        scope=profile.scope,
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
    )
    acquisition = AcquisitionService(
        bundle=bundle,
        ledger=ledger,
        quarantine=quarantine,
        connector=connector,
    ).run(request)
    assert acquisition.status == "completed"
    return bundle, profile, ledger, acquisition


def _payload(
    *,
    scope=None,
    operation: str | None = None,
    operation_decision: str = "approved",
    result: str = "pass",
) -> ReviewDecisionPayload:
    operation_reviews = ()
    if operation is not None:
        operation_reviews = (
            SourceOperationReview(
                operation=operation,
                decision=operation_decision,
                conditions=("Synthetic fixture only; no production rights conclusion.",),
            ),
        )
    return ReviewDecisionPayload(
        checklist=(
            ReviewCheck(
                check_id="synthetic-check",
                result=result,
                note="Mechanism-only synthetic review fixture.",
            ),
        ),
        source_operation_decisions=operation_reviews,
        confirmed_scope=scope,
        limitations=(
            "Synthetic reviewer evidence never counts toward KnowledgeRelease.",
        ),
    )


def _source_review_packet(bundle, profile, ledger, acquisition):
    return ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind=ReviewSubjectKind.SOURCE_CANDIDATE,
        subject_ref=acquisition.candidate_refs[0],
        gate=GovernanceGate.SOURCE_PROMOTION,
        scope=profile.scope,
        generated_by="system:test-packet-builder",
        known_limitations=(
            "Candidate and snapshot are synthetic and cannot establish production rights.",
        ),
        evidence_refs=(acquisition.snapshot_refs[0],),
        open_gap_refs=acquisition.gap_refs,
    )


def _approve_source_synthetically(bundle, profile, ledger, acquisition):
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    directory = _reviewers("knowledge_curator", "rights_officer")
    service = ReviewEventService(
        bundle=bundle, ledger=ledger, reviewers=directory
    )
    curator_ref = service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-knowledge_curator",
        reviewer_role="knowledge_curator",
        axis="metadata_quality",
        decision="approved",
        rationale="Synthetic metadata mechanism check passed.",
        decision_payload=_payload(),
    )
    rights_ref = service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-rights_officer",
        reviewer_role="rights_officer",
        axis="rights",
        decision="approved",
        rationale="Synthetic rights mechanism check passed only for fixture flow.",
        decision_payload=_payload(operation="register_link_metadata"),
    )
    decisions = ReviewLedgerDecisionProvider(bundle=bundle, ledger=ledger)
    gate_decision = decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    )
    assert gate_decision is not None
    assert gate_decision.synthetic is True
    assert gate_decision.production_eligible is False
    source_ref = SourcePromotionService(
        bundle=bundle,
        ledger=ledger,
        decisions=decisions,
    ).promote(
        source_id="synthetic-reviewed-source",
        candidate_ref=acquisition.candidate_refs[0],
        snapshot_ref=acquisition.snapshot_refs[0],
        promoted_by="system:synthetic-review-flow",
    )
    return packet_ref, (curator_ref, rights_ref), decisions, source_ref


def test_review_packet_pins_exact_subject_scope_gaps_and_safety_boundary(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    packet = ReviewPacket.model_validate(ledger.get(packet_ref).payload)

    assert packet.subject.subject_kind == "source_candidate"
    assert packet.subject.object_ref == acquisition.candidate_refs[0]
    assert packet.subject_payload_sha256 == acquisition.candidate_refs[0].entry_sha256
    assert packet.gate == "source_promotion"
    assert packet.requested_roles == ("knowledge_curator", "rights_officer")
    assert packet.requested_source_operations == ("register_link_metadata",)
    assert packet.source_policy is not None
    assert packet.source_policy.policy_id == "nmpa-cn-regulatory-metadata"
    assert packet.scope == profile.scope
    assert packet.evidence_refs == (acquisition.snapshot_refs[0],)
    assert packet.open_gap_refs == acquisition.gap_refs
    assert packet.synthetic is True
    assert packet.contains_patient_data is False
    assert packet.knowledge_effect == "informational_only"
    assert packet.runtime_authority == "none"


def test_review_packet_discovers_related_open_gaps_when_caller_omits_them(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind=ReviewSubjectKind.SOURCE_CANDIDATE,
        subject_ref=acquisition.candidate_refs[0],
        gate=GovernanceGate.SOURCE_PROMOTION,
        scope=profile.scope,
        generated_by="system:test-packet-builder",
        known_limitations=(
            "Machine-created gaps must be carried even when omitted by the caller.",
        ),
        evidence_refs=(acquisition.snapshot_refs[0],),
    )
    packet = ReviewPacket.model_validate(ledger.get(packet_ref).payload)

    assert packet.open_gap_refs == acquisition.gap_refs


def test_snapshot_content_packet_derives_policy_and_persistence_operation(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind="source_snapshot",
        subject_ref=acquisition.snapshot_refs[0],
        gate="content_persistence",
        scope=profile.scope,
        generated_by="system:test",
        known_limitations=("Synthetic blob only.",),
        evidence_refs=(acquisition.candidate_refs[0],),
    )
    packet = ReviewPacket.model_validate(ledger.get(packet_ref).payload)

    assert packet.source_policy is not None
    assert packet.source_policy.policy_id == "nmpa-cn-regulatory-metadata"
    assert packet.requested_source_operations == ("persist_snapshot",)
    assert packet.requested_roles == ("rights_officer", "knowledge_curator")


def test_change_set_packet_uses_content_persistence_gate_and_carries_gaps(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind="change_set",
        subject_ref=acquisition.change_set_refs[0],
        gate="content_persistence",
        scope=profile.scope,
        generated_by="system:test",
        known_limitations=("Synthetic change observation only.",),
        evidence_refs=(acquisition.snapshot_refs[0],),
    )
    packet = ReviewPacket.model_validate(ledger.get(packet_ref).payload)

    assert packet.source_policy is not None
    assert packet.requested_source_operations == ("persist_snapshot",)
    assert packet.requested_roles == ("rights_officer", "knowledge_curator")
    assert packet.open_gap_refs == acquisition.gap_refs


def test_packet_builder_rejects_stale_subject(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    connector = OfflineFixtureConnector(
        _fixture_root(), catalog_sha256=FIXTURE_CATALOG_SHA256
    )
    # A second run appends a successor for the same logical candidate.
    request = AcquisitionRequest(
        request_id="review-readiness-second",
        validation_profile_id=profile.profile_id,
        trigger="scheduled",
        policy_ids=("nmpa-cn-regulatory-metadata",),
        topic_codes=(
            {
                "system": "urn:continucare:synthetic-topic",
                "version": "1",
                "code": "review-readiness",
            },
        ),
        query_terms=("medication followup",),
        scope=profile.scope,
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
    )
    AcquisitionService(
        bundle=bundle,
        ledger=ledger,
        quarantine=QuarantineBlobStore(tmp_path / "quarantine"),
        connector=connector,
    ).run(request)

    with pytest.raises(KnowledgeOpsPolicyError, match="stale subject"):
        _source_review_packet(bundle, profile, ledger, acquisition)


def test_synthetic_review_events_are_append_only_and_never_count_toward_release(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref, event_refs, decisions, _ = _approve_source_synthetically(
        bundle, profile, ledger, acquisition
    )

    for event_ref in event_refs:
        event = ReviewEvent.model_validate(ledger.get(event_ref).payload)
        assert event.decision == "approved"
        assert event.reviewer_assurance == "synthetic_test"
        assert event.synthetic is True
        assert event.counts_toward_release is False
        assert event.knowledge_effect == "informational_only"
        assert event.runtime_authority == "none"
    decision = decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    )
    assert decision is not None
    assert decision.production_eligible is False
    assert ledger.get(packet_ref).synthetic is True


def test_latest_review_event_head_controls_gate_without_history_rewrite(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    directory = _reviewers("knowledge_curator", "rights_officer")
    service = ReviewEventService(bundle=bundle, ledger=ledger, reviewers=directory)
    first = service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-knowledge_curator",
        reviewer_role="knowledge_curator",
        axis="metadata_quality",
        decision="approved",
        rationale="First synthetic decision.",
        decision_payload=_payload(),
    )
    second = service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-knowledge_curator",
        reviewer_role="knowledge_curator",
        axis="metadata_quality",
        decision="changes_requested",
        rationale="Synthetic change request supersedes the decision head.",
    )
    service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-rights_officer",
        reviewer_role="rights_officer",
        axis="rights",
        decision="approved",
        rationale="Synthetic rights decision.",
        decision_payload=_payload(operation="register_link_metadata"),
    )
    decisions = ReviewLedgerDecisionProvider(bundle=bundle, ledger=ledger)

    assert first.record_version == 1
    assert second.record_version == 2
    assert second.entry_sha256 != first.entry_sha256
    assert decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    ) is None
    third = service.record(
        packet_ref=packet_ref,
        reviewer_identity_id="synthetic-knowledge_curator",
        reviewer_role="knowledge_curator",
        axis="metadata_quality",
        decision="approved",
        rationale="Synthetic corrected decision.",
        decision_payload=_payload(),
    )
    assert third.record_version == 3
    assert decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    ) is not None
    assert ledger.history(first.collection, first.record_id)[0].ref == first


def test_new_related_gap_invalidates_existing_gate_decision(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    _, _, decisions, _ = _approve_source_synthetically(
        bundle, profile, ledger, acquisition
    )
    assert decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    ) is not None

    observed_at = datetime.now(timezone.utc)
    gap = KnowledgeGap(
        gap_id="late-scope-gap",
        gap_kind="clinical_scope_missing",
        scope=profile.scope,
        subject_ref=acquisition.candidate_refs[0],
        reason="A later machine observation requires a new review packet.",
        blocks=("source_promotion", "knowledge_release"),
        observed_at=observed_at,
        synthetic=True,
    )
    ledger.append(
        LedgerCollection.GAP,
        gap.gap_id,
        payload_type="knowledge_gap",
        payload=gap,
        recorded_by="system:test",
        recorded_at=observed_at,
        synthetic=True,
    )

    assert decisions.resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    ) is None


def test_new_packet_invalidates_events_on_older_packet(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    first_packet = _source_review_packet(bundle, profile, ledger, acquisition)
    second_packet = _source_review_packet(bundle, profile, ledger, acquisition)
    assert second_packet.record_version == 2
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=_reviewers("knowledge_curator"),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="stale Review Packet"):
        service.record(
            packet_ref=first_packet,
            reviewer_identity_id="synthetic-knowledge_curator",
            reviewer_role="knowledge_curator",
            axis="metadata_quality",
            decision="approved",
            rationale="Must not use stale packet.",
            decision_payload=_payload(),
        )


def test_review_service_rejects_role_axis_mismatch(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=_reviewers("rights_officer"),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="axis"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id="synthetic-rights_officer",
            reviewer_role="rights_officer",
            axis="clinical",
            decision="approved",
            rationale="Invalid synthetic role/axis pair.",
            decision_payload=_payload(operation="register_link_metadata"),
        )


def test_multi_role_gate_requires_distinct_reviewer_identities(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    reviewer = ReviewerIdentity(
        identity_id="synthetic-multi-role",
        display_name="Synthetic multi-role fixture",
        roles=("knowledge_curator", "rights_officer"),
        assurance="synthetic_test",
        synthetic=True,
    )
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=InMemoryReviewerDirectory((reviewer,)),
    )
    service.record(
        packet_ref=packet_ref,
        reviewer_identity_id=reviewer.identity_id,
        reviewer_role="knowledge_curator",
        axis="metadata_quality",
        decision="approved",
        rationale="Synthetic curator check.",
        decision_payload=_payload(),
    )
    service.record(
        packet_ref=packet_ref,
        reviewer_identity_id=reviewer.identity_id,
        reviewer_role="rights_officer",
        axis="rights",
        decision="approved",
        rationale="Synthetic rights check.",
        decision_payload=_payload(operation="register_link_metadata"),
    )

    assert ReviewLedgerDecisionProvider(
        bundle=bundle, ledger=ledger
    ).resolve_gate(
        acquisition.candidate_refs[0], GovernanceGate.SOURCE_PROMOTION
    ) is None


def test_unverified_real_identity_cannot_approve(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    reviewer = ReviewerIdentity(
        identity_id="unverified-curator",
        display_name="Unverified curator",
        roles=("knowledge_curator",),
        assurance="identity_unverified",
        synthetic=False,
    )
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=InMemoryReviewerDirectory((reviewer,)),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="cannot approve"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id=reviewer.identity_id,
            reviewer_role="knowledge_curator",
            axis="metadata_quality",
            decision="approved",
            rationale="Unverified identity cannot approve.",
            decision_payload=_payload(),
        )


def test_formal_identity_contract_requires_auditable_verification_evidence():
    with pytest.raises(ValidationError, match="formal reviewer requires"):
        ReviewerIdentity(
            identity_id="not-a-formal-reviewer",
            display_name="No formal reviewer available",
            roles=("clinical_reviewer",),
            assurance="formally_verified",
            synthetic=False,
        )


def test_readiness_directory_cannot_assert_a_formal_production_reviewer():
    reviewer = ReviewerIdentity(
        identity_id="formal-mechanism-fixture",
        display_name="Mechanism-only formal identity fixture",
        roles=("clinical_reviewer",),
        assurance="formally_verified",
        synthetic=False,
        verification_reference="urn:continucare:test:identity-proof",
        verification_evidence_sha256="1" * 64,
        verified_by="test:identity-authority",
        verified_at=datetime.now(timezone.utc),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="cannot assert formal"):
        InMemoryReviewerDirectory((reviewer,))


def test_rights_approval_requires_exact_operation_decisions(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=_reviewers("rights_officer"),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="explicitly approve"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id="synthetic-rights_officer",
            reviewer_role="rights_officer",
            axis="rights",
            decision="approved",
            rationale="Missing exact operation decision.",
            decision_payload=_payload(),
        )
    with pytest.raises(KnowledgeOpsPolicyError, match="explicitly approve"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id="synthetic-rights_officer",
            reviewer_role="rights_officer",
            axis="rights",
            decision="approved",
            rationale="Operation remains needs verification.",
            decision_payload=_payload(
                operation="register_link_metadata",
                operation_decision="needs_verification",
            ),
        )


def test_approved_checklist_cannot_contain_failure(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    packet_ref = _source_review_packet(bundle, profile, ledger, acquisition)
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=_reviewers("knowledge_curator"),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="no failures"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id="synthetic-knowledge_curator",
            reviewer_role="knowledge_curator",
            axis="metadata_quality",
            decision="approved",
            rationale="Failing checklist cannot approve.",
            decision_payload=_payload(result="fail"),
        )


def test_synthetic_review_provider_drives_only_synthetic_source_promotion(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    _, _, _, source_ref = _approve_source_synthetically(
        bundle, profile, ledger, acquisition
    )
    source = GovernedSourceV2.model_validate(ledger.get(source_ref).payload)

    assert source.registry_status == "synthetic_fixture"
    assert source.production_eligible is False
    assert source.synthetic is True
    assert source.runtime_authority == "none"
    assert source.unresolved_gap_refs == acquisition.gap_refs
    assert ledger.head(LedgerCollection.CLAIM, "any") is None
    assert ledger.head(LedgerCollection.BINDING, "any") is None


def _synthetic_release_context(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    _, _, decisions, source_ref = _approve_source_synthetically(
        bundle, profile, ledger, acquisition
    )
    candidate = KnowledgeReleaseCandidate(
        release_candidate_id="synthetic-cn-zh-release",
        intended_uses=(
            "internal_knowledge_operations",
            "acquisition_basis_explanation",
            "informational_display",
        ),
        governance_bundle_id=bundle.index.bundle_id,
        governance_bundle_version=bundle.index.bundle_version,
        governance_manifests=_manifest_evidence(bundle),
        artifacts=(
            ReleaseArtifact(
                artifact_kind="source",
                object_ref=source_ref,
                validation_profile_id=profile.profile_id,
                scope=profile.scope,
            ),
        ),
        blocking_gap_refs=acquisition.gap_refs,
        created_at=datetime.now(timezone.utc),
        created_by="system:synthetic-release-test",
        synthetic=True,
    )
    readiness = ReleaseReadinessService(
        bundle=bundle, ledger=ledger, decisions=decisions
    )
    candidate_ref = readiness.stage_candidate(candidate)
    packet_ref = ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind="knowledge_release",
        subject_ref=candidate_ref,
        gate="knowledge_release",
        scope=profile.scope,
        generated_by="system:test",
        known_limitations=(
            "All artifacts and reviewers in this packet are synthetic fixtures.",
        ),
        additional_required_roles=("pharmacist",),
        open_gap_refs=acquisition.gap_refs,
    )
    reviewers = _reviewers(
        "knowledge_curator", "rights_officer", "clinical_reviewer", "pharmacist"
    )
    event_service = ReviewEventService(
        bundle=bundle, ledger=ledger, reviewers=reviewers
    )
    reviews = (
        ("knowledge_curator", "release", _payload()),
        ("rights_officer", "release", _payload()),
        ("clinical_reviewer", "release", _payload(scope=profile.scope)),
        ("pharmacist", "applicability", _payload(scope=profile.scope)),
    )
    for role, axis, payload in reviews:
        event_service.record(
            packet_ref=packet_ref,
            reviewer_identity_id=f"synthetic-{role}",
            reviewer_role=role,
            axis=axis,
            decision="approved",
            rationale=f"Synthetic {role} release-readiness mechanism test.",
            decision_payload=payload,
        )
    return bundle, profile, ledger, acquisition, readiness, candidate_ref, source_ref


def test_release_readiness_blocks_synthetic_reviews_sources_and_open_gaps(tmp_path):
    _, _, ledger, acquisition, readiness, candidate_ref, source_ref = (
        _synthetic_release_context(tmp_path)
    )
    report_ref = readiness.assess(candidate_ref)
    report = ReleaseReadinessReport.model_validate(ledger.get(report_ref).payload)
    codes = {item.code for item in report.blockers}

    assert report.ready is False
    assert report.inspected_artifact_count == 1
    assert report.production_review_count == 0
    assert "synthetic_release_candidate" in codes
    assert "governance_release_intent_blocked" in codes
    assert "synthetic_artifact" in codes
    assert "nonproduction_source" in codes
    assert "artifact_review_nonproduction" in codes
    assert "open_blocking_gap" in codes
    assert "release_review_nonproduction" in codes
    assert "release_review_missing" not in codes
    assert "release_required_role_missing" not in codes
    assert len(
        [item for item in report.blockers if item.code == "open_blocking_gap"]
    ) == len(acquisition.gap_refs)
    assert any(item.subject_ref == source_ref for item in report.blockers)
    assert report.knowledge_effect == "informational_only"
    assert report.runtime_authority == "none"


def test_finalize_records_blocked_report_but_never_creates_release(tmp_path):
    _, _, ledger, _, readiness, candidate_ref, _ = _synthetic_release_context(tmp_path)

    with pytest.raises(KnowledgeReleaseBlocked) as captured:
        readiness.finalize(candidate_ref, finalized_by="system:test")

    report = ReleaseReadinessReport.model_validate(
        ledger.get(captured.value.readiness_report_ref).payload
    )
    assert report.ready is False
    assert ledger.head(LedgerCollection.RELEASE, "synthetic-cn-zh-release") is None


def test_empty_release_intent_is_explicitly_not_ready(tmp_path):
    bundle = load_builtin_ops_bundle()
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    decisions = ReviewLedgerDecisionProvider(bundle=bundle, ledger=ledger)
    readiness = ReleaseReadinessService(
        bundle=bundle, ledger=ledger, decisions=decisions
    )
    candidate = KnowledgeReleaseCandidate(
        release_candidate_id="empty-release",
        intended_uses=("internal_knowledge_operations",),
        governance_bundle_id=bundle.index.bundle_id,
        governance_bundle_version=bundle.index.bundle_version,
        governance_manifests=_manifest_evidence(bundle),
        artifacts=(),
        blocking_gap_refs=(),
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
        synthetic=False,
    )
    candidate_ref = readiness.stage_candidate(candidate)
    report_ref = readiness.assess(candidate_ref)
    report = ReleaseReadinessReport.model_validate(ledger.get(report_ref).payload)

    assert report.ready is False
    assert {item.code for item in report.blockers} == {
        "governance_release_intent_blocked",
        "empty_release",
        "release_review_missing",
    }


def test_release_cannot_hide_gaps_carried_by_promoted_source(tmp_path):
    bundle, profile, ledger, acquisition = _acquisition_context(tmp_path)
    _, _, decisions, source_ref = _approve_source_synthetically(
        bundle, profile, ledger, acquisition
    )
    readiness = ReleaseReadinessService(
        bundle=bundle, ledger=ledger, decisions=decisions
    )
    observed_at = datetime.now(timezone.utc)
    late_gap = KnowledgeGap(
        gap_id="late-release-gap",
        gap_kind="production_evidence_missing",
        scope=profile.scope,
        subject_ref=source_ref,
        reason="A later current-head Gap must be discovered independently.",
        blocks=("knowledge_release",),
        observed_at=observed_at,
        synthetic=True,
    )
    ledger.append(
        LedgerCollection.GAP,
        late_gap.gap_id,
        payload_type="knowledge_gap",
        payload=late_gap,
        recorded_by="system:test",
        recorded_at=observed_at,
        synthetic=True,
    )
    candidate = KnowledgeReleaseCandidate(
        release_candidate_id="omitted-gap-release",
        intended_uses=("informational_display",),
        governance_bundle_id=bundle.index.bundle_id,
        governance_bundle_version=bundle.index.bundle_version,
        governance_manifests=_manifest_evidence(bundle),
        artifacts=(
            ReleaseArtifact(
                artifact_kind="source",
                object_ref=source_ref,
                validation_profile_id=profile.profile_id,
                scope=profile.scope,
            ),
        ),
        blocking_gap_refs=(),
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
        synthetic=True,
    )
    candidate_ref = readiness.stage_candidate(candidate)
    report = ReleaseReadinessReport.model_validate(
        ledger.get(readiness.assess(candidate_ref)).payload
    )

    assert len(
        [item for item in report.blockers if item.code == "open_blocking_gap"]
    ) == len(acquisition.gap_refs) + 1


def test_release_stage_rejects_mismatched_governance_manifest_evidence(tmp_path):
    bundle = load_builtin_ops_bundle()
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    readiness = ReleaseReadinessService(
        bundle=bundle,
        ledger=ledger,
        decisions=ReviewLedgerDecisionProvider(bundle=bundle, ledger=ledger),
    )
    evidence = list(_manifest_evidence(bundle))
    evidence[0] = {**evidence[0], "manifest_sha256": "0" * 64}
    candidate = KnowledgeReleaseCandidate(
        release_candidate_id="manifest-mismatch",
        intended_uses=("internal_knowledge_operations",),
        governance_bundle_id=bundle.index.bundle_id,
        governance_bundle_version=bundle.index.bundle_version,
        governance_manifests=tuple(evidence),
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
        synthetic=False,
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="does not match"):
        readiness.stage_candidate(candidate)
    assert ledger.verify_all() == 0


def test_synthetic_reviewer_cannot_attach_to_nonsynthetic_release_packet(tmp_path):
    bundle = load_builtin_ops_bundle()
    profile = bundle.coverage_profiles[0]
    ledger = AppendOnlyLedger(tmp_path / "ledger")
    readiness = ReleaseReadinessService(
        bundle=bundle,
        ledger=ledger,
        decisions=ReviewLedgerDecisionProvider(bundle=bundle, ledger=ledger),
    )
    candidate = KnowledgeReleaseCandidate(
        release_candidate_id="nonsynthetic-empty",
        intended_uses=("internal_knowledge_operations",),
        governance_bundle_id=bundle.index.bundle_id,
        governance_bundle_version=bundle.index.bundle_version,
        governance_manifests=_manifest_evidence(bundle),
        created_at=datetime.now(timezone.utc),
        created_by="system:test",
        synthetic=False,
    )
    candidate_ref = readiness.stage_candidate(candidate)
    packet_ref = ReviewPacketBuilder(bundle=bundle, ledger=ledger).build(
        subject_kind="knowledge_release",
        subject_ref=candidate_ref,
        gate="knowledge_release",
        scope=profile.scope,
        generated_by="system:test",
        known_limitations=("No artifacts and no formal reviewers.",),
    )
    service = ReviewEventService(
        bundle=bundle,
        ledger=ledger,
        reviewers=_reviewers("knowledge_curator"),
    )

    with pytest.raises(KnowledgeOpsPolicyError, match="non-synthetic subject"):
        service.record(
            packet_ref=packet_ref,
            reviewer_identity_id="synthetic-knowledge_curator",
            reviewer_role="knowledge_curator",
            axis="release",
            decision="approved",
            rationale="Synthetic identity must not review real release subject.",
            decision_payload=_payload(),
        )


def test_builtin_release_intent_contains_no_fake_approval_or_artifact():
    bundle = load_builtin_ops_bundle()
    read_model = load_builtin_ops_read_model()

    assert bundle.release_intent.status == "readiness_only_blocked"
    assert bundle.release_intent.selected_artifact_count == 0
    assert bundle.release_intent.formal_reviewers_available is False
    assert bundle.release_intent.formal_license_decisions_available is False
    assert bundle.release_intent.release_ready is False
    assert bundle.release_intent.knowledge_effect == "informational_only"
    assert bundle.release_intent.runtime_authority == "none"
    assert read_model.release_intent == bundle.release_intent
    assert read_model.production_releases == ()


def test_release_contract_rejects_runtime_authority_and_clinical_rules():
    bundle = load_builtin_ops_bundle()
    payload = {
        "release_candidate_id": "invalid-runtime-release",
        "intended_uses": ["informational_display"],
        "governance_bundle_id": bundle.index.bundle_id,
        "governance_bundle_version": bundle.index.bundle_version,
        "governance_manifests": _manifest_evidence(bundle),
        "artifacts": [],
        "blocking_gap_refs": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "system:test",
        "synthetic": False,
        "runtime_authority": "clinical",
    }
    with pytest.raises(ValidationError):
        KnowledgeReleaseCandidate.model_validate(payload)
    payload["runtime_authority"] = "none"
    payload["clinical_rule_refs"] = [None]
    with pytest.raises(ValidationError):
        KnowledgeReleaseCandidate.model_validate(payload)


def test_readiness_report_cannot_claim_ready_with_blockers():
    with pytest.raises(ValidationError, match="exactly when blockers are empty"):
        ReleaseReadinessReport.model_validate(
            {
                "report_id": "invalid-report",
                "release_candidate_ref": {
                    "collection": "release_candidate",
                    "record_id": "candidate",
                    "record_version": 1,
                    "entry_sha256": "0" * 64,
                },
                "ready": True,
                "blockers": [
                    {
                        "code": ReadinessBlockerCode.EMPTY_RELEASE,
                        "message": "Blocked.",
                    }
                ],
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "assessed_by": "system:test",
                "inspected_artifact_count": 0,
                "production_review_count": 0,
            }
        )
