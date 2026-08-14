from __future__ import annotations

import pytest

from continucare.knowledge.ops.models import KnowledgeOpsPolicyError
from continucare.knowledge.ops.security import assert_no_sensitive_data


def test_internal_review_ids_do_not_trigger_phone_number_false_positives() -> None:
    assert_no_sensitive_data(
        {
            "event_id": "event-17255792435b1b6f15e5",
            "attestation_id": "attest-17255792435b1b6f15e5aabbccddeeff",
        }
    )


def test_internal_id_exemption_does_not_weaken_forbidden_patient_keys() -> None:
    with pytest.raises(KnowledgeOpsPolicyError, match="data key is prohibited"):
        assert_no_sensitive_data({"patient_id": "event-17255792435b1b6f15e5"})
    with pytest.raises(KnowledgeOpsPolicyError, match="appears to contain personal data"):
        assert_no_sensitive_data({"event_id": "patient@example.com"})
