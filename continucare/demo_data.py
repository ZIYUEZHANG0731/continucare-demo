"""Synthetic-only fixtures used by the deterministic demo."""

DEMO_PATIENT_ID = "P-DEMO-001"

NORMAL_MESSAGE = "今天有点恶心，但是能正常喝水，没有吐。"
L2_MESSAGE = "今天吐了一次，喝水也不太想喝。"
L4_MESSAGE = "我现在胸口很痛，还有点喘不过气。"

SCENARIOS = {
    "正常路径": NORMAL_MESSAGE,
    "L2 工作流": L2_MESSAGE,
    "L4 红旗": L4_MESSAGE,
}

