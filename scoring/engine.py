"""Score calculation and feedback-driven weight adjustment."""
from .rules import (ORG_TYPE_BASE_SCORES, WEALTH_INDICATOR_SCORES,
                    COMPONENT_WEIGHTS, DEFAULT_BASE_SCORE, DEFAULT_WEALTH_SCORE)
from db import queries


def score_org(org: dict, radius_km: float,
              weight_overrides: dict[str, float] | None = None) -> tuple[float, dict]:
    """
    Compute a priority score (0.0–1.0) for an organisation.
    Returns (score, breakdown_dict).
    """
    org_type = org.get("org_type", "")

    # Type score — use feedback-blended override if available
    if weight_overrides and org_type in weight_overrides:
        type_score = weight_overrides[org_type]
    else:
        type_score = ORG_TYPE_BASE_SCORES.get(org_type, DEFAULT_BASE_SCORE)

    # Wealth indicator — static, not feedback-adjusted (structural property of org type)
    wealth_score = WEALTH_INDICATOR_SCORES.get(org_type, DEFAULT_WEALTH_SCORE)

    # Distance score — linear decay from 1.0 at 0 km to 0.0 at radius_km
    distance_km = org.get("distance_km") or radius_km
    distance_score = max(0.0, 1.0 - (distance_km / radius_km))

    # Data completeness
    completeness_score = _completeness(org)

    breakdown = {
        "type_score":       round(type_score, 3),
        "wealth_indicator": round(wealth_score, 3),
        "distance_score":   round(distance_score, 3),
        "completeness":     round(completeness_score, 3),
    }

    score = (
        COMPONENT_WEIGHTS["type_score"]       * type_score
        + COMPONENT_WEIGHTS["wealth_indicator"] * wealth_score
        + COMPONENT_WEIGHTS["distance_score"]   * distance_score
        + COMPONENT_WEIGHTS["completeness"]     * completeness_score
    )

    return round(min(score, 1.0), 4), breakdown


def _completeness(org: dict) -> float:
    fields = ["name", "address_line1", "town", "postcode", "phone", "email", "website"]
    filled = sum(1 for f in fields if org.get(f))
    return filled / len(fields)


def get_feedback_weights() -> dict[str, float]:
    """
    Compute adjusted type weights per org_type from feedback data.
    Blends static base (0.7) with empirical conversion rate (0.3).
    """
    counts = queries.get_feedback_counts_by_type()
    weights = {}
    for org_type, data in counts.items():
        static = ORG_TYPE_BASE_SCORES.get(org_type, DEFAULT_BASE_SCORE)
        conversion_rate = data["converted"] / max(data["contacted"], 1)
        blended = (static * 0.7) + (conversion_rate * 0.3)
        weights[org_type] = round(blended, 4)
        queries.upsert_scoring_weight(
            org_type, blended,
            data["contacted"], data["converted"]
        )
    return weights


def recalculate_scores_for_run(run_id: int, radius_km: float):
    """Re-score all 'new' leads in a run using latest feedback weights."""
    feedback_weights = get_feedback_weights()
    leads = queries.get_leads_for_run(run_id)
    for lead in leads:
        if lead["status"] != "new":
            continue
        score, breakdown = score_org(lead, radius_km, feedback_weights)
        queries.upsert_lead(lead["org_id"], run_id, score, breakdown)
