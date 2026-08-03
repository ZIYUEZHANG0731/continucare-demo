"""Evaluate the controlled Summary outline against the configured live MiMo API."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from continucare.care_agent.model_api import SemanticModelConfig
from continucare.fhir.observations import build_patient_reported_observation
from continucare.fhir.terminology import BODY_WEIGHT, NAUSEA_FINDING
from continucare.layer4 import (
    ClinicalMemoryService,
    ClinicalStateService,
    ControlledSummaryService,
    ControlledSummaryStatus,
    EvidenceReference,
    Layer4InputSnapshot,
    Layer4SQLiteStore,
    MiMoControlledSummaryAdapter,
    StateMetricDefinition,
    SummaryAgentTask,
    SummaryFact,
    SummaryFactLedger,
    render_summary_outline,
    validate_summary_outline,
)
from continucare.layer4.contracts import EvidenceRole, ResourceReference


DEFAULT_CASES = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "summary_live_cases_v1.json"
)
DEFAULT_OUTPUT = Path("/tmp/continucare-summary-live-evaluation.json")
EXPECTED_MODEL = "mimo-v2.5"
EXPECTED_PROMPT = "mimo-summary-outline-v1"
PERIOD_START = "2026-08-01T00:00:00+00:00"
PERIOD_END = "2026-08-02T12:00:00+00:00"
ASSEMBLED_AT = "2026-08-02T12:01:00+00:00"
UCUM = "http://unitsofmeasure.org"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _case_facts(case: dict[str, Any]) -> list[SummaryFact]:
    raw_facts = case.get("facts")
    if raw_facts is None:
        count = int(case["generated_fact_count"])
        section = case["generated_section"]
        template = case["generated_text_template"]
        raw_facts = [
            {
                "fact_id": f"fact-{case['case_id']}-{index:02d}",
                "kind": "metric_state",
                "section": section,
                "canonical_text": template.format(index=index),
                "priority": 50,
            }
            for index in range(1, count + 1)
        ]
    facts: list[SummaryFact] = []
    for index, raw in enumerate(raw_facts, start=1):
        evidence_id = f"evidence-{case['case_id']}-{index:02d}"
        payload = dict(raw)
        mandatory = payload.pop("mandatory", True)
        requires_confirmation = payload.pop(
            "requires_doctor_confirmation", False
        )
        facts.append(
            SummaryFact(
                **payload,
                mandatory=mandatory,
                requires_doctor_confirmation=requires_confirmation,
                evidence_refs=[
                    EvidenceReference(
                        evidence_id=evidence_id,
                        resource=ResourceReference(
                            reference=(
                                f"Observation/summary-eval-{case['case_id']}-{index:02d}"
                            ),
                            version_id="1",
                        ),
                        role=EvidenceRole.SOURCE,
                        effective_start=PERIOD_END,
                        effective_end=PERIOD_END,
                        evidence_text="synthetic controlled-summary evaluation fact",
                    )
                ],
            )
        )
    return facts


def _ledger(case: dict[str, Any]) -> SummaryFactLedger:
    return SummaryFactLedger(
        ledger_id=f"summary-live-ledger-{case['case_id']}",
        patient_id="SYNTHETIC-SUMMARY-EVAL",
        pathway_code="SYNTHETIC-DYNAMIC-PATHWAY",
        pathway_version="1.0.0",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        assembled_at=ASSEMBLED_AT,
        facts=_case_facts(case),
    )


def _evaluate_case(
    adapter: MiMoControlledSummaryAdapter,
    case: dict[str, Any],
) -> dict[str, Any]:
    ledger = _ledger(case)
    task = SummaryAgentTask(
        task_id=f"summary-live-task-{case['case_id']}",
        ledger=ledger,
    )
    outcome = adapter.organize(task)
    validate_summary_outline(ledger, outcome.decision)
    rendered = render_summary_outline(outcome.decision, ledger)
    facts = {item.fact_id: item for item in ledger.facts}
    used_ids = [
        fact_id for group in outcome.decision.groups for fact_id in group.fact_ids
    ]
    expected_evidence = {
        evidence.evidence_id
        for fact in ledger.facts
        for evidence in fact.evidence_refs
    }
    actual_evidence = {
        evidence.evidence_id
        for item in rendered
        for evidence in item.evidence_refs
    }
    local_text_exact = all(
        item.text
        == "\n".join(facts[fact_id].canonical_text for fact_id in group.fact_ids)
        for group, item in zip(outcome.decision.groups, rendered, strict=True)
    )
    doctor_confirmation_exact = all(
        item.requires_doctor_confirmation
        == any(
            facts[fact_id].requires_doctor_confirmation
            for fact_id in group.fact_ids
        )
        for group, item in zip(outcome.decision.groups, rendered, strict=True)
    )
    checks = {
        "live_provider_mode": outcome.provider in {"xiaomi_mimo", "mimo"},
        "model_version_exact": outcome.model_name == EXPECTED_MODEL,
        "prompt_version_exact": outcome.prompt_version == EXPECTED_PROMPT,
        "all_fact_ids_exactly_once": (
            len(used_ids) == len(set(used_ids)) == len(ledger.facts)
            and set(used_ids) == set(facts)
        ),
        "local_canonical_text_exact": local_text_exact,
        "evidence_coverage_exact": actual_evidence == expected_evidence,
        "doctor_confirmation_exact": doctor_confirmation_exact,
        "strict_outline_only": set(outcome.decision.model_dump(mode="json"))
        == {"groups"},
        "usage_recorded": bool(outcome.model_usage),
        "request_id_recorded": bool(outcome.provider_request_id),
    }
    outline_json = _canonical_json(outcome.decision.model_dump(mode="json"))
    rendered_json = _canonical_json(
        [item.model_dump(mode="json") for item in rendered]
    )
    return {
        "case_id": case["case_id"],
        "passed": all(checks.values()),
        "fact_count": len(ledger.facts),
        "group_count": len(outcome.decision.groups),
        "latency_ms": outcome.latency_ms,
        "attempt_count": outcome.attempt_count,
        "model_usage": outcome.model_usage,
        "provider_request_id": outcome.provider_request_id,
        "outline_digest": hashlib.sha256(outline_json.encode("utf-8")).hexdigest(),
        "local_render_digest": hashlib.sha256(
            rendered_json.encode("utf-8")
        ).hexdigest(),
        "checks": checks,
        "outline": outcome.decision.model_dump(mode="json"),
    }


class _MutableInputReader:
    def __init__(self, snapshot: Layer4InputSnapshot):
        self.snapshot = snapshot

    def read(self, patient_id: str) -> Layer4InputSnapshot:
        if patient_id != self.snapshot.patient_id:
            raise ValueError("synthetic evaluation patient mismatch")
        return self.snapshot


class _CapturingAdapter:
    def __init__(self, delegate: MiMoControlledSummaryAdapter):
        self.delegate = delegate
        self.config = delegate.config
        self.VERSION = delegate.VERSION
        self.tasks: list[SummaryAgentTask] = []
        self.outcomes = []

    @property
    def configured(self) -> bool:
        return self.delegate.configured

    def organize(self, task: SummaryAgentTask):
        self.tasks.append(task)
        outcome = self.delegate.organize(task)
        self.outcomes.append(outcome)
        return outcome


def _weight(observation_id: str, effective_time: str, value: float) -> dict:
    return build_patient_reported_observation(
        observation_id=observation_id,
        patient_id="P-DEMO-001",
        questionnaire_response_id=f"response-{observation_id}",
        effective_time=effective_time,
        code=BODY_WEIGHT,
        value_element="valueQuantity",
        value={
            "value": value,
            "unit": "kg",
            "system": UCUM,
            "code": "kg",
        },
    )


def _nausea(observation_id: str, effective_time: str) -> dict:
    return build_patient_reported_observation(
        observation_id=observation_id,
        patient_id="P-DEMO-001",
        questionnaire_response_id=f"response-{observation_id}",
        effective_time=effective_time,
        code=NAUSEA_FINDING,
        value_element="valueBoolean",
        value=True,
    )


def _evaluate_service_end_to_end(
    adapter: MiMoControlledSummaryAdapter,
) -> dict[str, Any]:
    patient_id = "P-DEMO-001"
    pathway_code = "SYNTHETIC-DYNAMIC-PATHWAY"
    pathway_version = "1.0.0"
    snapshot = Layer4InputSnapshot(
        patient_id=patient_id,
        observations=[
            _weight("summary-e2e-weight-1", "2026-08-01T10:00:00+00:00", 70),
            _weight("summary-e2e-weight-2", "2026-08-02T10:00:00+00:00", 71),
            _nausea("summary-e2e-nausea", "2026-08-02T09:00:00+00:00"),
        ],
        assembled_at=PERIOD_END,
    )
    reader = _MutableInputReader(snapshot)
    with tempfile.TemporaryDirectory(
        prefix="continucare-summary-live-e2e-"
    ) as directory:
        repository = Layer4SQLiteStore(Path(directory) / "summary-live-e2e.db")
        memory = ClinicalMemoryService(
            reader,
            repository,
            pathway_code=pathway_code,
            pathway_version=pathway_version,
        )
        states = ClinicalStateService(
            reader,
            repository,
            pathway_code=pathway_code,
            pathway_version=pathway_version,
        )
        memory.rebuild(patient_id)
        state = states.build(
            patient_id=patient_id,
            definitions=[
                StateMetricDefinition(
                    metric_id="body-weight",
                    version="1.0.0",
                    pathway_code=pathway_code,
                    pathway_version=pathway_version,
                    code_system=BODY_WEIGHT.system,
                    code=BODY_WEIGHT.code,
                    display="Body weight",
                    unit="kg",
                    unit_system=UCUM,
                    lookback_hours=72,
                    stale_after_hours=24,
                    trend_window_hours=72,
                ),
                StateMetricDefinition(
                    metric_id="nausea-present",
                    version="1.0.0",
                    pathway_code=pathway_code,
                    pathway_version=pathway_version,
                    code_system=NAUSEA_FINDING.system,
                    code=NAUSEA_FINDING.code,
                    display="Nausea present",
                    lookback_hours=72,
                    stale_after_hours=24,
                    trend_window_hours=72,
                ),
                StateMetricDefinition(
                    metric_id="doctor-defined-e2e-metric",
                    version="1.0.0",
                    pathway_code=pathway_code,
                    pathway_version=pathway_version,
                    code_system="urn:synthetic:doctor-metric",
                    code="e2e-metric",
                    display="Doctor-defined E2E metric",
                    lookback_hours=72,
                    stale_after_hours=24,
                    trend_window_hours=72,
                ),
            ],
            as_of=PERIOD_END,
        )
        capturing = _CapturingAdapter(adapter)
        service = ControlledSummaryService(
            memory,
            repository,
            model_adapter=capturing,
        )
        first = service.generate(
            patient_id=patient_id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_at=ASSEMBLED_AT,
        )
        repeated = service.generate(
            patient_id=patient_id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            generated_at="2026-08-02T12:02:00+00:00",
        )
        task = capturing.tasks[0]
        expected_text = sorted(
            fact.canonical_text for fact in task.ledger.facts
        )
        actual_text = sorted(
            line for item in first.summary.items for line in item.text.splitlines()
        )
        expected_evidence = {
            evidence.evidence_id
            for fact in task.ledger.facts
            for evidence in fact.evidence_refs
        }
        actual_evidence = {
            evidence.evidence_id
            for item in first.summary.items
            for evidence in item.evidence_refs
        }
        provenance_id = first.summary.provenance_refs[0].reference.removeprefix(
            "Provenance/"
        )
        provenance = repository.get_fhir_resource("Provenance", provenance_id)
        checks = {
            "service_llm_assisted": (
                first.status == ControlledSummaryStatus.LLM_ASSISTED
                and first.summary.generation_mode == "llm_assisted"
            ),
            "dynamic_state_snapshot_bound": (
                first.summary.source_state_snapshot_reference
                == (
                    f"urn:continucare:state-snapshot:{state.snapshot_id}"
                    f":version:{state.version}"
                )
            ),
            "all_fact_ids_persisted": (
                set(first.summary.source_fact_ids)
                == {fact.fact_id for fact in task.ledger.facts}
            ),
            "local_canonical_text_exact": actual_text == expected_text,
            "evidence_coverage_exact": actual_evidence == expected_evidence,
            "model_audit_metadata_persisted": all(
                (
                    first.summary.model_provider,
                    first.summary.model_name,
                    first.summary.prompt_version,
                    first.summary.agent_version,
                    first.summary.model_usage,
                    first.summary.provider_request_id,
                    first.summary.outline_digest,
                )
            ),
            "provenance_persisted": bool(
                provenance
                and any(
                    item["what"]["reference"]
                    == first.summary.source_state_snapshot_reference
                    for item in provenance.get("entity", [])
                )
            ),
            "idempotent_without_second_model_call": (
                repeated.summary == first.summary and len(capturing.tasks) == 1
            ),
        }
        usage = first.summary.model_usage or {}
        model_outcome = capturing.outcomes[0]
        return {
            "case_id": "controlled_summary_service_end_to_end",
            "passed": all(checks.values()),
            "fact_count": first.fact_count,
            "group_count": len(first.summary.items),
            "latency_ms": model_outcome.latency_ms,
            "attempt_count": model_outcome.attempt_count,
            "model_usage": usage,
            "provider_request_id": first.summary.provider_request_id,
            "outline_digest": first.summary.outline_digest,
            "local_render_digest": hashlib.sha256(
                _canonical_json(
                    [
                        item.model_dump(mode="json")
                        for item in first.summary.items
                    ]
                ).encode("utf-8")
            ).hexdigest(),
            "checks": checks,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate controlled Summary fact-ID organization with synthetic data "
            "against the configured live MiMo API. This is not clinical validation."
        )
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fail-on-mismatch", action="store_true")
    args = parser.parse_args()

    config = SemanticModelConfig.from_environment()
    adapter = MiMoControlledSummaryAdapter(config)
    if not config.summary_llm_enabled or not adapter.configured:
        raise SystemExit(
            "Enable CONTINUCARE_USE_SUMMARY_LLM and configure the official MiMo "
            "provider with a local secret before running this evaluation."
        )
    if (
        config.model_name != EXPECTED_MODEL
        or config.summary_prompt_version != EXPECTED_PROMPT
    ):
        raise SystemExit(
            "Configured model or Summary Prompt does not match the frozen evaluation "
            f"boundary: {EXPECTED_MODEL} / {EXPECTED_PROMPT}."
        )

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    details: list[dict[str, Any]] = []
    for case in cases:
        try:
            details.append(_evaluate_case(adapter, case))
        except Exception as exc:
            details.append(
                {
                    "case_id": case["case_id"],
                    "passed": False,
                    "error_type": type(exc).__name__,
                }
            )

    try:
        details.append(_evaluate_service_end_to_end(adapter))
    except Exception as exc:
        details.append(
            {
                "case_id": "controlled_summary_service_end_to_end",
                "passed": False,
                "error_type": type(exc).__name__,
            }
        )

    total_tokens = sum(
        (item.get("model_usage") or {}).get("total_tokens", 0)
        for item in details
    )
    total_latency_ms = sum(item.get("latency_ms", 0) for item in details)
    passed = sum(bool(item["passed"]) for item in details)
    output = {
        "release_id": "continucare-layer4-controlled-summary-v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": (
            "single_run_synthetic_engineering_evaluation_not_clinical_validation"
        ),
        "provider": config.provider,
        "model": config.model_name,
        "prompt_version": config.summary_prompt_version,
        "adapter_version": adapter.VERSION,
        "renderer_version": "controlled-summary-renderer-v1",
        "case_set": args.cases.name,
        "totals": {
            "cases": len(details),
            "passed": passed,
            "total_facts": sum(item.get("fact_count", 0) for item in details),
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency_ms,
            "average_latency_ms": (
                round(total_latency_ms / len(details)) if details else 0
            ),
        },
        "all_passed": passed == len(details),
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"report: {args.output}")
    if args.fail_on_mismatch and not output["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
