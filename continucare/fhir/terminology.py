"""Verified terminology constants for the first GLP-1 evidence pack.

Every concept in this module has an authoritative source entry in
``docs/clinical/glp1_14d_observation_evidence.md``. Adding a code without adding
its source and a terminology test is prohibited by the conformance policy.
"""

from __future__ import annotations

from dataclasses import dataclass

FHIR_OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
LOINC = "http://loinc.org"
SNOMED_CT = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"
LOINC_VERSION = "2.82"


@dataclass(frozen=True)
class CodingDefinition:
    system: str
    code: str
    display: str
    version: str | None = None

    def as_fhir(self) -> dict[str, str]:
        coding = {"system": self.system, "code": self.code, "display": self.display}
        if self.version:
            coding["version"] = self.version
        return coding


NAUSEA_FINDING = CodingDefinition(SNOMED_CT, "422587007", "Nausea")
VOMITING_COUNT_24H = CodingDefinition(
    LOINC, "94070-0", "Emesis count 24 hour", LOINC_VERSION
)
FLUID_INTAKE_24H_ESTIMATED = CodingDefinition(
    LOINC, "75301-2", "Fluid intake 24 hour Estimated", LOINC_VERSION
)
ABDOMINAL_PAIN_FINDING = CodingDefinition(
    SNOMED_CT, "21522001", "Abdominal pain (finding)"
)
BODY_WEIGHT = CodingDefinition(LOINC, "29463-7", "Body weight", LOINC_VERSION)

NAUSEA_PRESENCE = CodingDefinition(
    LOINC, "81660-3", "Nausea [Presence]", LOINC_VERSION
)
NAUSEA_NONE = CodingDefinition(LOINC, "LA137-2", "None", LOINC_VERSION)
NAUSEA_MILD = CodingDefinition(LOINC, "LA6752-5", "Mild", LOINC_VERSION)
NAUSEA_MODERATE = CodingDefinition(LOINC, "LA6751-7", "Moderate", LOINC_VERSION)
NAUSEA_SEVERE = CodingDefinition(LOINC, "LA6750-9", "Severe", LOINC_VERSION)
NAUSEA_RESOLVED = CodingDefinition(LOINC, "LA9041-0", "Resolved", LOINC_VERSION)
