from __future__ import annotations

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.services.followup import FollowUpService


def test_database_persists_after_reopen(tmp_path):
    db_path = tmp_path / "persistent.db"
    first_store = SQLiteStore(db_path)
    message = FollowUpService(first_store).submit_message(
        DEMO_PATIENT_ID, "合成随访内容"
    )

    reopened_store = SQLiteStore(db_path)
    persisted = reopened_store.get_message(message.message_id)

    assert persisted is not None
    assert persisted.message_text == "合成随访内容"
    assert reopened_store.get_patient(DEMO_PATIENT_ID).synthetic is True


def test_patient_submission_writes_audit_event(tmp_path):
    store = SQLiteStore(tmp_path / "audit.db")
    message = FollowUpService(store).submit_message(DEMO_PATIENT_ID, "合成输入")

    events = store.list_audit_events(DEMO_PATIENT_ID)

    assert len(events) == 1
    assert events[0].entity_id == message.message_id
    assert events[0].event_type == "patient_message_submitted"
