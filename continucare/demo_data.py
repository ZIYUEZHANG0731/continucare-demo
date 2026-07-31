"""Synthetic-only fixtures for FHIR collection and traceability demos."""

DEMO_PATIENT_ID = "P-DEMO-001"

NAUSEA_MESSAGE = "今天有点恶心，但是能正常喝水，没有吐。"
QUANTIFIED_MESSAGE = "今天吐了一次，估计过去24小时喝水800毫升。"
UNSTRUCTURED_MESSAGE = "我现在胸口很痛，还有点喘不过气。"

SCENARIOS = {
    "恶心记录": NAUSEA_MESSAGE,
    "呕吐与摄入记录": QUANTIFIED_MESSAGE,
    "仅保留患者原文": UNSTRUCTURED_MESSAGE,
}
