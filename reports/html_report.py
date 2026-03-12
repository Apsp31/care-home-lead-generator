"""HTML report generator using Jinja2."""
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from db import queries
from scoring.rules import QUALIFICATION_NOTES

TEMPLATE_DIR = Path(__file__).parent / "templates"

ORG_TYPE_LABELS = {
    # Hospital departments
    "hospital_private":     "Private Patient Units",
    "hospital_discharge":   "Hospital Discharge / Transfer of Care Teams",
    "hospital_frailty":     "Frailty & Elderly Care Units",
    "hospital_dementia":    "Memory Clinics & Dementia Services",
    "hospital_ortho":       "Trauma & Orthopaedics Departments",
    "hospital_stroke":      "Stroke Rehabilitation Units",
    "hospital_social_work": "Hospital Social Work Departments",
    # Primary care
    "GP":                   "GP Surgeries",
    "PCN":                  "Primary Care Networks",
    # Clinical
    "hospice":              "Hospices",
    "pharmacy":             "Pharmacies",
    # Professional referrers
    "solicitor":            "Solicitors (Wills, LPA & Probate)",
    "wealth_manager":       "Wealth & Fund Managers",
    "financial_adviser":    "Independent Financial Advisers",
    "estate_agent":         "Estate Agents (Later Living)",
    # Statutory
    "social_services":      "Adult Social Services",
    # Community — specialist
    "dementia_cafe":        "Dementia Cafes & Memory Cafes",
    "age_uk_branch":        "Age UK / Age Concern Branches",
    "carers_group":         "Carers Support Groups",
    "day_centre":           "Elderly Day Centres",
    # Community — general
    "community_group":      "Community Groups",
    "place_of_worship":     "Places of Worship",
    "nursing_home":         "Other Care & Nursing Homes",
}

# Display order — highest wealth indicator / referral priority first
TYPE_ORDER = list(ORG_TYPE_LABELS.keys())


def generate_report(run_id: int, output_path: str | None = None) -> str:
    """
    Generate an HTML report for a search run.
    Returns the HTML string. Optionally writes to output_path.
    """
    run = queries.get_search_run(run_id)
    if not run:
        raise ValueError(f"Search run {run_id} not found")

    leads = queries.get_leads_for_run(run_id)

    # Attach and pre-process contacts for each lead
    for lead in leads:
        all_contacts = queries.get_contacts_for_org(lead["org_id"])
        lead["contacts"] = all_contacts

        # Split into real (named/has details) vs pure placeholders
        lead["real_contacts"] = [
            c for c in all_contacts
            if c.get("name") or c.get("email") or c.get("phone")
        ]
        placeholders = [
            c for c in all_contacts
            if not c.get("name") and not c.get("email") and not c.get("phone")
        ]
        lead["placeholder_hint"] = " · ".join(c["role"] for c in placeholders)

        # Parse score_breakdown JSON string → dict for template use
        import json as _json
        raw_bd = lead.get("score_breakdown")
        lead["score_breakdown"] = _json.loads(raw_bd) if isinstance(raw_bd, str) and raw_bd else {}

    # Group by org_type, sorted by priority within each type
    type_groups: dict[str, list] = {}
    for lead in leads:
        t = lead.get("org_type", "other")
        type_groups.setdefault(t, []).append(lead)

    for t in type_groups:
        type_groups[t].sort(key=lambda x: x["priority_score"], reverse=True)

    sections = []
    ordered = TYPE_ORDER + [t for t in type_groups if t not in TYPE_ORDER]
    for org_type in ordered:
        if org_type in type_groups:
            sections.append({
                "label": ORG_TYPE_LABELS.get(org_type, org_type.replace("_", " ").title()),
                "org_type": org_type,
                "qualification_note": QUALIFICATION_NOTES.get(org_type, ""),
                "leads": type_groups[org_type],
            })

    # Stats
    total = len(leads)
    high_priority = sum(1 for l in leads if l["priority_score"] >= 0.7)
    mid_priority = sum(1 for l in leads if 0.4 <= l["priority_score"] < 0.7)
    contacted = sum(1 for l in leads if l["status"] in ("contacted", "converted", "not_converted"))
    converted = sum(1 for l in leads if l["status"] == "converted")
    conversion_rate = converted / max(contacted, 1) if contacted else 0.0

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("report.html")
    html = template.render(
        run=run,
        sections=sections,
        total_leads=total,
        high_priority=high_priority,
        mid_priority=mid_priority,
        contacted=contacted,
        converted=converted,
        conversion_rate=conversion_rate,
        generated_at=datetime.now().strftime("%d %b %Y %H:%M"),
    )

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")

    return html
