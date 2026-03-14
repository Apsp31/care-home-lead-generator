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
    "hospital_chc":         "continuing healthcare coordinator",
    "hospital_ot_discharge":"occupational therapist discharge",
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


def enrich_website_contacts(orgs: list[dict], max_orgs: int = 60) -> list[dict]:
    """
    For orgs with a website but no phone/email, scrape /contact and /about-us pages
    to extract contact details. Rate-limited; caps at max_orgs to bound run time.
    """
    import re
    import requests as _req

    _EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,7}\b')
    _PHONE_RE = re.compile(r'\b0\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b')
    _PATHS = ["/contact", "/contact-us", "/about-us", "/about", "/contactus"]
    _BAD_EMAIL = {"example.", "noreply", "no-reply", "test@", "info@example", "user@"}

    count = 0
    for org in orgs:
        if count >= max_orgs:
            break
        website = (org.get("website") or "").strip().rstrip("/")
        if not website.startswith("http"):
            continue
        if org.get("phone") and org.get("email"):
            continue

        for path in _PATHS:
            try:
                resp = _req.get(
                    website + path,
                    timeout=5,
                    allow_redirects=True,
                    headers={"User-Agent": "CareHomeLeadGenerator/1.0"},
                )
                if resp.status_code != 200:
                    continue
                text = resp.text[:50000]
                if not org.get("phone"):
                    m = _PHONE_RE.search(text)
                    if m:
                        org["phone"] = re.sub(r'\s+', ' ', m.group(0)).strip()
                if not org.get("email"):
                    emails = _EMAIL_RE.findall(text)
                    good = [e for e in emails
                            if not any(b in e.lower() for b in _BAD_EMAIL)]
                    if good:
                        org["email"] = good[0]
                if org.get("phone") and org.get("email"):
                    break
            except Exception:
                continue

        count += 1
    return orgs


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
