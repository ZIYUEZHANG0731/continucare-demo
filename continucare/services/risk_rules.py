"""Clinically gated workflow prioritization.

No GLP-1 escalation threshold is active until a named clinical owner approves a
versioned rule with a source that applies to the selected product and population.
"""

from __future__ import annotations

from continucare.models import Observation, RiskDecision


def evaluate_risk(observations: list[Observation]) -> RiskDecision:
    """Return no clinical classification while the governed rule set is empty."""

    return RiskDecision(severity="not_assessed", create_alert=False)
