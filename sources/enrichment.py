"""Named contact enrichment via LinkedIn/DuckDuckGo for orgs without named contacts.

Searches LinkedIn via DDG for named individuals (e.g. practice managers, discharge
liaison nurses, private client solicitors) for orgs that have no named contacts yet.
"""
import time

try:
    from .web_search import _ddg, _parse_linkedin_profile, _DDG_AVAILABLE, _DELAY
except ImportError:
    _DDG_AVAILABLE = False
    _DELAY = 2.0
    def _ddg(*a, **kw): return []
    def _parse_linkedin_profile(*a, **kw): return None

# Best LinkedIn search phrase per org type
ENRICH_ROLES: dict[str, str] = {
    "GP":                   "practice manager",
    "hospital_discharge":   "discharge liaison nurse",
    "hospital_private":     "private patient coordinator",
    "hospital_frailty":     "consultant geriatrician frailty",
    "hospital_dementia":    "dementia specialist nurse",
    "hospital_ortho":       "orthopaedic liaison nurse",
    "hospital_stroke":      "stroke coordinator",
    "hospital_social_work": "hospital social worker discharge",
    "solicitor":            "private client solicitor wills",
    "financial_adviser":    "independent financial adviser care fees",
    "wealth_manager":       "wealth manager",
    "hospice":              "hospice referral coordinator",
    "social_services":      "adult social care team manager",
    "dementia_cafe":        "dementia cafe coordinator",
    "age_uk_branch":        "age uk information advice",
    "carers_group":         "carers support coordinator",
}


def enrich_contacts(orgs: list[dict], enabled_types: set[str] | None = None) -> list[dict]:
    """
    For each org in enabled_types without a named contact, search LinkedIn
    via DuckDuckGo for a named individual. Augments org['contacts'] in-place.
    If enabled_types is None, enrich all types that have a role defined.
    """
    if not _DDG_AVAILABLE:
        print("[enrichment] ddgs not installed — skipping contact enrichment")
        return orgs

    target_types = enabled_types if enabled_types is not None else set(ENRICH_ROLES)

    for org in orgs:
        org_type = org.get("org_type", "")
        if org_type not in target_types:
            continue

        # Skip if already has a named contact
        if any(c.get("name") for c in org.get("contacts", [])):
            continue

        role = ENRICH_ROLES.get(org_type)
        if not role:
            continue

        # Strip dept suffix for hospital names ("Royal Free — Private Patient Unit" → "Royal Free")
        raw_name = org.get("name", "")
        search_name = raw_name.split(" — ")[0] if " — " in raw_name else raw_name
        if not search_name:
            continue

        query = f'site:linkedin.com/in "{role}" "{search_name}"'
        time.sleep(_DELAY)

        for hit in _ddg(query, max_results=4):
            href = hit.get("href", "")
            if "linkedin.com/in/" not in href:
                continue
            contact = _parse_linkedin_profile(hit.get("title", ""), href)
            if contact:
                org.setdefault("contacts", []).append(contact)
                break  # one named contact per org is sufficient

    return orgs
