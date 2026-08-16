"""Seed one synthetic nurse shift with multiple human-review outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.care_engine.mapping import map_response_to_observations
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.fhir.questionnaires import (
    build_questionnaire_response,
    questionnaire_response_summary,
)
from continucare.layer4.fhir import build_patient_confirmed_review_task
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.models import AuditEvent, FollowUpMessage
from continucare.pathways.mappings import ObservationMappingPolicy
from continucare.patient_mobile import canonical_db_path
from continucare.services.competition_demo import (
    read_competition_demo,
    start_competition_demo,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.manual_review_workflow import ManualReviewWorkflowService
from continucare.services.supplemental_reports import (
    read_supplemental_reports,
    resolve_supplemental_turn,
    submit_supplemental_report_turn,
)


_DAY_TASKS = (
    (
        "0735",
        7,
        35,
        {
            "body-weight": {
                "value": 75.0,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "nausea-present": False,
            "vomiting-count-24h": 0,
            "fluid-intake-24h-estimated": {
                "value": 1500,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "abdominal-pain-present": False,
            "free-text-report": "昨晚睡眠一般，早晨能够正常喝水，没有恶心或腹痛。",
        },
        "reviewed_no_escalation",
        "已核对患者晨间记录、时间窗和饮水估计值；护士本次人工决定不转交医生。",
    ),
    (
        "0910",
        9,
        10,
        {
            "body-weight": {
                "value": 74.6,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "nausea-present": True,
            "nausea-severity": "LA6752-5",
            "vomiting-count-24h": 0,
            "fluid-intake-24h-estimated": {
                "value": 900,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "abdominal-pain-present": False,
            "free-text-report": "早餐后有一点恶心，没有呕吐，也没有腹痛。",
        },
        "clarification_required",
        "护士核对后希望患者补充说明恶心持续时间以及饮水估计的计算方式。",
    ),
    (
        "1125",
        11,
        25,
        {
            "body-weight": {
                "value": 74.2,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "nausea-present": True,
            "nausea-severity": "LA6751-7",
            "vomiting-count-24h": 2,
            "fluid-intake-24h-estimated": {
                "value": 600,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "abdominal-pain-present": True,
            "free-text-report": "上午恶心比较明显，记录了两次呕吐，现在还有腹部不舒服。",
        },
        "escalated_to_doctor",
        "护士逐项核对患者原话和本次回答后，人工决定请医生进一步评估；该决定不是系统阈值触发。",
    ),
    (
        "1405",
        14,
        5,
        {
            "body-weight": {
                "value": 73.9,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "nausea-present": False,
            "vomiting-count-24h": 0,
            "fluid-intake-24h-estimated": {
                "value": 1200,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "abdominal-pain-present": False,
            "free-text-report": "午后状态平稳，能少量多次喝水，目前没有恶心和腹痛。",
        },
        "reviewed_no_escalation",
        "护士已核对午后患者记录和原话；本次只记录人工未上报决定，不表示临床安全。",
    ),
    (
        "1810",
        18,
        10,
        {
            "body-weight": {
                "value": 73.6,
                "unit": "kg",
                "system": "http://unitsofmeasure.org",
                "code": "kg",
            },
            "nausea-present": True,
            "nausea-severity": "LA6752-5",
            "vomiting-count-24h": 1,
            "abdominal-pain-present": False,
            "free-text-report": "傍晚又有一点恶心，饮水量记不清楚，今天记录过一次呕吐。",
        },
        None,
        None,
    ),
)

def _seed_task(
    *,
    store: SQLiteStore,
    repository: Layer4SQLiteStore,
    service: ManualReviewWorkflowService,
    questionnaire: dict,
    policy: ObservationMappingPolicy,
    session,
    code: str,
    authored_at: datetime,
    answers: dict,
    outcome: str | None,
    note: str | None,
) -> None:
    authored = authored_at.isoformat()
    response_id = f"response-nurse-day-{code}"
    response = build_questionnaire_response(
        questionnaire=questionnaire,
        response_id=response_id,
        patient_id=DEMO_PATIENT_ID,
        authored=authored,
        answers=answers,
    )
    message_text, _ = questionnaire_response_summary(response, questionnaire)
    store.save_message(
        FollowUpMessage(
            message_id=response_id,
            patient_id=DEMO_PATIENT_ID,
            message_text=message_text,
            submitted_at=authored,
            source="synthetic_nurse_day_fixture",
            processing_status="structured_complete",
        )
    )
    store.save_questionnaire_response(response, questionnaire)
    observations = map_response_to_observations(
        response=response,
        questionnaire=questionnaire,
        policy=policy,
    )
    store.save_observations(observations)
    task_id = f"task-nurse-day-{code}"
    task = build_patient_confirmed_review_task(
        patient_id=DEMO_PATIENT_ID,
        receipt_digest=hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
        questionnaire_response_reference=f"QuestionnaireResponse/{response_id}",
        observation_references=[
            f"Observation/{item.observation_id}" for item in observations
        ],
        pathway_reference=(
            f"urn:continucare:pathway:{session.pathway_code}|{session.pathway_version}"
        ),
        authored_on=authored,
        task_id=task_id,
    )
    repository.save_fhir_resource(task, patient_id=DEMO_PATIENT_ID)
    store.append_audit_event(
        AuditEvent(
            event_id="audit-" + uuid5(NAMESPACE_URL, f"{task_id}|created").hex,
            patient_id=DEMO_PATIENT_ID,
            entity_type="Task",
            entity_id=task_id,
            event_type="manual_review_task_created",
            actor_type="synthetic_day_fixture",
            details_json={
                "synthetic_only": True,
                "clinical_assessment": "not_assessed",
                "fixture": "nurse_complete_day",
            },
            created_at=authored,
        )
    )
    received_at = (authored_at + timedelta(minutes=4)).isoformat()
    started_at = (authored_at + timedelta(minutes=6)).isoformat()
    service.acknowledge(
        patient_id=DEMO_PATIENT_ID,
        task_id=task_id,
        note="合成班次：护士已接手记录。",
        occurred_at=received_at,
    )
    service.start(
        patient_id=DEMO_PATIENT_ID,
        task_id=task_id,
        note="合成班次：护士开始逐项人工复核。",
        occurred_at=started_at,
    )
    if outcome and note:
        result = service.record_outcome(
            patient_id=DEMO_PATIENT_ID,
            task_id=task_id,
            outcome=outcome,
            note=note,
            occurred_at=(authored_at + timedelta(minutes=12)).isoformat(),
        )
        if result.communication is not None:
            service.approve_draft(
                patient_id=DEMO_PATIENT_ID,
                task_id=task_id,
                communication_id=result.communication["id"],
                note="合成班次：护士已逐字核对沟通文字；没有执行真实发送。",
                occurred_at=(authored_at + timedelta(minutes=15)).isoformat(),
            )


def _synthetic_adapter() -> MiMoSemanticAdapter:
    key_name = "CONTINUCARE_NURSE_DAY_SYNTHETIC_KEY"
    os.environ[key_name] = "synthetic-fixture-only"
    config = SemanticModelConfig(
        provider="volcengine_doubao",
        model_name="doubao-seed-2-0-lite-260215",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env=key_name,
        prompt_version="doubao-semantic-extraction-v1",
        timeout_seconds=2,
    )

    def transport(_url, _headers, _payload, _timeout):
        return {
            "id": "synthetic-nurse-day-request",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"blocked": False, "items": []},
                            ensure_ascii=False,
                        ),
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    return MiMoSemanticAdapter(config, transport=transport)


def seed_nurse_day_demo(
    db_path: Path | str, *, spread_over_five_days: bool = False
) -> dict[str, int]:
    """Replace a local synthetic DB and seed one shift or a five-day story."""

    path = Path(db_path)
    initial = start_competition_demo(path)
    store = SQLiteStore(path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    run = store.get_agent_run(initial.run_id)
    candidate_ids = [item["candidate_id"] for item in run.output_json["candidates"]]
    ConfirmedReviewService(store, care_agent=agent, care_engine=engine).accept_all(
        run.run_id,
        candidate_ids,
    )
    progress = read_competition_demo(path)
    session = store.get_care_session(progress.session_id)
    questionnaire = engine.questionnaire_for_session(session)

    before_supplemental = read_supplemental_reports(path, session_id=session.session_id)
    submitted = submit_supplemental_report_turn(
        path,
        session_id=session.session_id,
        expected_story_generation=progress.generation,
        expected_supplemental_generation=before_supplemental.generation,
        message_text="午后散步回来觉得左手有点僵，想补充告诉护士。",
        synthetic_confirmed=True,
        model_adapter=_synthetic_adapter(),
    )
    resolve_supplemental_turn(
        path,
        session_id=session.session_id,
        run_id=submitted.pending_run_id,
        decision="accepted",
        expected_story_generation=read_competition_demo(path).generation,
        expected_supplemental_generation=submitted.generation,
    )

    store = SQLiteStore(path, initialize=False)
    repository = Layer4SQLiteStore(path, initialize=False)
    service = ManualReviewWorkflowService(store, layer4_store=repository)
    local_day = datetime.now().astimezone().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    policy = engine.observation_mapping_for_session(session)
    for index, (code, hour, minute, answers, outcome, note) in enumerate(_DAY_TASKS):
        day_offset = index - (len(_DAY_TASKS) - 1) if spread_over_five_days else 0
        _seed_task(
            store=store,
            repository=repository,
            service=service,
            questionnaire=questionnaire,
            policy=policy,
            session=session,
            code=code,
            authored_at=(local_day + timedelta(days=day_offset)).replace(
                hour=hour, minute=minute
            ),
            answers=answers,
            outcome=outcome,
            note=note,
        )

    final_tasks = repository.list_fhir_resources(
        patient_id=DEMO_PATIENT_ID,
        resource_type="Task",
        current_only=True,
    )
    final_supplemental = read_supplemental_reports(path, session_id=session.session_id)
    return {
        "tasks": len(final_tasks),
        "pending": sum(item.get("status") != "completed" for item in final_tasks),
        "completed": sum(item.get("status") == "completed" for item in final_tasks),
        "supplemental": len(final_supplemental.reports),
        "record_days": 5 if spread_over_five_days else 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=canonical_db_path())
    parser.add_argument(
        "--five-days",
        action="store_true",
        help="spread the five synthetic review records across five calendar days",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            seed_nurse_day_demo(
                args.db, spread_over_five_days=args.five_days
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
