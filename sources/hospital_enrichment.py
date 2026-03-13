"""Hospital-specific contact enrichment.

For each unique hospital (parent name across its dept leads):
  1. PALS contact — always run; one DDG call per hospital; reliable gateway contact.
  2. Trust website dept pages — run on full enrichment opt-in; extracts NHS email/phone.
  3. NHS Jobs postings — run on full enrichment opt-in; postings often include contact details.
"""
import re
import time
from urllib.parse import urlparse

from .web_search import _ddg, _DELAY

_EMAIL_RE = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,7}\b')
_PHONE_RE = re.compile(r'\b0\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b')
_NHS_SUFFIXES = ('.nhs.uk', '.nhs.net')

# Dept-specific search keywords for trust website / NHS Jobs
DEPT_KEYWORDS: dict[str, str] = {
    'hospital_discharge':    'discharge team "transfer of care"',
    'hospital_chc':          '"continuing healthcare" CHC',
    'hospital_private':      '"private patients" OR "private patient unit"',
    'hospital_frailty':      'frailty "elderly care"',
    'hospital_dementia':     '"memory clinic" OR "dementia service"',
    'hospital_ortho':        'trauma orthopaedics',
    'hospital_stroke':       '"stroke unit" OR "stroke rehabilitation"',
    'hospital_social_work':  '"hospital social work" OR "adult social care"',
    'hospital_ot_discharge': '"occupational therapy" discharge',
}


def _emails(text: str) -> list[str]:
    """Return NHS emails first, then any other emails found."""
    found = _EMAIL_RE.findall(text)
    nhs = [e for e in found if any(e.lower().endswith(s) for s in _NHS_SUFFIXES)]
    return nhs or found


def _phone(text: str) -> str:
    m = _PHONE_RE.search(text)
    return re.sub(r'\s+', ' ', m.group(0)).strip() if m else ''


def _pals_contact(hospital_name: str) -> dict | None:
    """One DDG call to find PALS email/phone for a hospital."""
    query = f'"{hospital_name}" PALS "patient advice" contact email phone'
    time.sleep(_DELAY)
    for hit in _ddg(query, max_results=4):
        body = hit.get('body', '') + ' ' + hit.get('title', '')
        emails = _emails(body)
        ph = _phone(body)
        if emails or ph:
            return {
                'name': '',
                'role': 'PALS — Patient Advice & Liaison Service',
                'email': emails[0] if emails else '',
                'phone': ph,
                'source_notes': (
                    'PALS is the gateway to any ward or department — '
                    'ask to be put through to the relevant team lead'
                ),
            }
    return None


def _dept_contacts(hospital_name: str, website: str, org_type: str) -> list[dict]:
    """Trust website + NHS Jobs search for a specific dept. Returns 0-2 contacts."""
    contacts = []
    kw = DEPT_KEYWORDS.get(org_type)
    if not kw:
        return contacts

    # 1. Trust website
    domain = ''
    if website:
        try:
            domain = urlparse(website).netloc.lstrip('www.')
        except Exception:
            pass
    if domain:
        query = f'site:{domain} {kw} contact email'
        time.sleep(_DELAY)
        for hit in _ddg(query, max_results=3):
            body = hit.get('body', '') + ' ' + hit.get('href', '')
            emails = _emails(body)
            ph = _phone(hit.get('body', ''))
            if emails or ph:
                contacts.append({
                    'name': '',
                    'role': f'{kw.strip(chr(34)).split(" OR ")[0].strip().title()} Contact',
                    'email': emails[0] if emails else '',
                    'phone': ph,
                    'source_notes': f'From trust website ({domain})',
                })
                break

    # 2. NHS Jobs
    query = f'site:jobs.nhs.uk "{hospital_name}" {kw}'
    time.sleep(_DELAY)
    for hit in _ddg(query, max_results=3):
        body = hit.get('body', '')
        emails = _emails(body)
        if emails:
            contacts.append({
                'name': '',
                'role': 'Dept Manager (from NHS Jobs posting)',
                'email': emails[0],
                'phone': _phone(body),
                'source_notes': 'Contact detail found in NHS Jobs vacancy listing',
            })
            break

    return contacts


def enrich_hospital_orgs(orgs: list[dict], full_enrichment: bool = False) -> list[dict]:
    """
    Enrich hospital department leads with real contact details.

    Always:
      - Adds PALS contact per unique parent hospital (1 DDG call each).

    full_enrichment=True (opt-in):
      - Searches trust website for dept email/phone.
      - Searches NHS Jobs for dept manager email.
    """
    # Group dept orgs by parent hospital name
    hospitals: dict[str, list[dict]] = {}
    for org in orgs:
        if not org.get('org_type', '').startswith('hospital_'):
            continue
        parent = org['name'].split(' — ')[0] if ' — ' in org['name'] else org['name']
        hospitals.setdefault(parent, []).append(org)

    for parent_name, dept_orgs in hospitals.items():
        # PALS — always
        pals = _pals_contact(parent_name)
        if pals:
            for dept_org in dept_orgs:
                if not any('PALS' in c.get('role', '') for c in dept_org.get('contacts', [])):
                    dept_org.setdefault('contacts', []).append(pals)

        # Full enrichment — trust website + NHS Jobs per dept
        if full_enrichment:
            website = next((o.get('website', '') for o in dept_orgs if o.get('website')), '')
            for dept_org in dept_orgs:
                extra = _dept_contacts(parent_name, website, dept_org.get('org_type', ''))
                dept_org.setdefault('contacts', []).extend(extra)

    return orgs
