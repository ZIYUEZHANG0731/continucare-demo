"""CN GLP-1 terminology whitelist compiled from the governed release."""

from __future__ import annotations

from functools import lru_cache

from continucare.agents.contracts import CodingContract
from continucare.fhir.questionnaires import flatten_questionnaire_items
from continucare.fhir.terminology import BODY_WEIGHT
from continucare.knowledge.compiler import compile_questionnaire
from continucare.knowledge.registry import load_cn_glp1_release
from continucare.terminology.catalog import (
    QuestionnaireBinding,
    SourceDocument,
    TerminologyCatalog,
)


@lru_cache(maxsize=1)
def load_cn_glp1_terminology_catalog() -> TerminologyCatalog:
    """Compile the CN Layer-3 whitelist from the locked L1 Questionnaire.

    The legacy DailyMed-derived dynamic symptom concepts deliberately do not
    enter this catalog. Until a CN-scoped terminology release is licensed and
    reviewed, Layer 3 may normalize only the fixed coded Questionnaire items;
    all other patient wording remains unstructured evidence for human review.
    """

    release = load_cn_glp1_release().release
    questionnaire = compile_questionnaire(release)
    terminology_codes = {
        (entry.coding.system, entry.coding.code, entry.coding.version)
        for entry in release.terminology
    }
    bindings: list[QuestionnaireBinding] = []
    for item in flatten_questionnaire_items(questionnaire.get("item", [])):
        codes = item.get("code", [])
        if not codes:
            continue
        if len(codes) != 1:
            raise ValueError(f"CN Questionnaire item {item['linkId']} must have one code")
        coding = CodingContract.model_validate(codes[0])
        if (
            coding.system,
            coding.code,
            coding.version,
        ) not in terminology_codes:
            raise ValueError(
                "CN Questionnaire code is absent from terminology manifest: "
                f"{item['linkId']}"
            )
        bindings.append(
            QuestionnaireBinding(
                link_id=item["linkId"],
                coding=coding,
                symptom_concept_id=None,
            )
        )

    # Body weight is governed by the versioned doctor goal-rule release rather
    # than the older symptom Questionnaire release.  It still joins the same
    # terminology resolution and MiMo confirmation path as every other field.
    bindings.append(
        QuestionnaireBinding(
            link_id="body-weight",
            coding=CodingContract(
                system=BODY_WEIGHT.system,
                code=BODY_WEIGHT.code,
                display=BODY_WEIGHT.display,
                version=BODY_WEIGHT.version,
            ),
            symptom_concept_id=None,
        )
    )

    source_documents = []
    for source_id in (
        "nhc-obesity-guideline-2024",
        "loinc-2.82",
        "continucare-glp1-14d-questionnaire-v1",
    ):
        source = next(item for item in release.sources if item.source_id == source_id)
        source_documents.append(
            SourceDocument(
                source_id=source.source_id,
                title=source.title,
                url=source.canonical_url,
                retrieved_on=source.retrieved_at,
            )
        )
    return TerminologyCatalog(
        catalog_id="continucare-cn-glp1-l1-terminology-whitelist",
        # This extends the existing L1 release with another governed binding;
        # keeping the release identifier aligned with the CareSession preserves
        # the cross-layer release boundary. The content digest below is what
        # makes the additive catalog change immutable and detectable.
        version=release.manifest.release_id,
        status="engineering_validated_synthetic_only",
        scope_statement=(
            "CN GLP1-14D fixed Questionnaire bindings only; no dynamic symptom "
            "concepts are authorized."
        ),
        completeness_statement=(
            "The whitelist is intentionally incomplete. Unknown patient wording "
            "must remain raw evidence and must not create an Observation."
        ),
        code_system_release=(
            "LOINC 2.82; fixed SNOMED CT codes remain synthetic-only while edition, "
            "CN applicability and redistribution permission are unresolved"
        ),
        validation_service="ContinuCare CN L1 cross-file validator",
        validation_date="2026-08-14",
        target_hospital_validation_required=True,
        source_documents=source_documents,
        questionnaire_bindings=bindings,
        concepts=[],
    )
