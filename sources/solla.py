"""SOLLA (Society of Later Life Advisers) member discovery via web and LinkedIn search.

SOLLA is a UK-specific accreditation for care fees financial advisers. This source
uses targeted DuckDuckGo queries to find SOLLA member firms and named advisers in
the target area.

Strategy:
  1. Web search for SOLLA firm websites in the area (most reliable)
  2. LinkedIn profile search with "United Kingdom" filter (avoids US results)
  3. LinkedIn company pages for care-fees firms
"""
import re
import time
from .base import DataSource

try:
    from ddgs import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDG_AVAILABLE = True
    except ImportError:
        _DDG_AVAILABLE = False

from .web_search import (
    _ddg, _parse_linkedin_profile, _url_id, _reverse_geocode_town,
    _clean_title, _NEWS_DOMAINS, _DELAY,
)

# Credential suffixes to strip from adviser names
_CREDENTIAL_RE = re.compile(
    r'\s+(DipFA|DipPFS|APFS|FPFS|CFPCM|IMC|SOLLA|CII|CISI|FCSI|AFPC|'
    r'Chartered Financial Planner|Independent Financial Adviser|'
    r'BA|BSc|MSc|MBA|CFA|FCIB|FCA)\b.*$',
    re.I
)

# Keywords that confirm a result is SOLLA / care fees relevant
_SOLLA_KW = {"solla", "later life", "care fees", "care fee", "laterlife", "care cost"}

# Noise domains specific to SOLLA searches (info sites, care home directories, home care agencies)
_SOLLA_NOISE_DOMAINS = {
    "moneyhelper.org.uk", "payingforcare.org", "moneysavingexpert.com",
    "citizensadvice.org.uk", "ageuk.org.uk", "alzheimers.org.uk",
    "which.co.uk", "thisismoney.co.uk", "unbiased.co.uk",
    "lottie.org", "carehome.co.uk", "homeinstead.co.uk",
    "visiting-angels.co.uk", "carebase.org.uk", "adviserbook.co.uk",
    "symponia.co.uk",  # trade body for later life advisers, not an IFA firm
}

# US/international location indicators in LinkedIn entries
_US_LOCATION_RE = re.compile(
    r'\b(United States|USA|Florida|California|New York|Texas|Chicago|'
    r'Canada|Australia|India|Singapore)\b',
    re.I
)

# Roles that indicate a care worker rather than a financial adviser
_CARE_WORKER_RE = re.compile(
    r'\b(care giver|carer|care worker|care assistant|care manager|home care|'
    r'nursing|nurse|occupational|social media|marketing|recruitment|'
    r'registered manager|director of care)\b',
    re.I
)


def _clean_name(raw: str) -> str:
    """Strip credential suffixes — 'Jane Smith DipFA SOLLA' → 'Jane Smith'."""
    clean = _CREDENTIAL_RE.sub('', raw).strip()
    if re.match(r'^[A-Za-z\-\']{2,}\s+[A-Za-z\-\']{2,}$', clean):
        return clean
    return raw


def _make_org(name: str, lat: float, lon: float, town: str,
              website: str, source_id: str, contacts: list[dict]) -> dict:
    return {
        "name": name,
        "org_type": "financial_adviser",
        "source": "solla",
        "source_id": source_id,
        "address_line1": "",
        "address_line2": "",
        "town": town,
        "postcode": "",
        "lat": lat,
        "lon": lon,
        "distance_km": 0.0,
        "phone": "",
        "email": "",
        "website": website,
        "contacts": contacts,
    }


def _get_county(lat: float, lon: float) -> str:
    """Try to get county/region for broader area searches."""
    import requests
    try:
        resp = requests.get(
            "https://api.postcodes.io/postcodes",
            params={"lon": lon, "lat": lat, "limit": 1},
            timeout=8,
        )
        if resp.status_code == 200:
            results = resp.json().get("result") or []
            if results:
                r = results[0]
                return r.get("admin_county") or r.get("admin_district") or ""
    except Exception:
        pass
    return ""


class SollaSource(DataSource):
    name = "solla"

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if not _DDG_AVAILABLE:
            print("[solla] ddgs not installed. Run: pip install ddgs")
            return []

        town = _reverse_geocode_town(lat, lon)
        if not town:
            print("[solla] Could not determine town from coordinates.")
            return []

        # Also get county for broader searches
        county = _get_county(lat, lon) or town

        print(f"[solla] Searching SOLLA advisers in: {town} / {county}")
        results: list[dict] = []
        seen: set[str] = set()

        def _add(org: dict):
            key = re.sub(r'\s+', ' ', org["name"].lower().strip())
            if key and key not in seen and len(key) > 3:
                seen.add(key)
                results.append(org)

        # ── 1. Web searches for SOLLA firm websites (primary) ────────────────
        web_queries = [
            f'"SOLLA" "care fees" "{town}"',
            f'"SOLLA accredited" "financial adviser" "{county}"',
            f'"SOLLA member" "care fees" "{county}"',
            f'"later life specialist" "care fees adviser" "{county}"',
            f'site:solla.org.uk "{county}"',
        ]
        for query in web_queries:
            time.sleep(_DELAY)
            for org in self._web_firms(query, lat, lon, town):
                _add(org)

        # ── 2. LinkedIn profiles — UK-specific SOLLA searches ─────────────────
        li_queries = [
            f'site:linkedin.com/in "SOLLA" "care fees" "United Kingdom"',
            f'site:linkedin.com/in "SOLLA" "financial adviser" "{county}"',
            f'site:linkedin.com/in "care fees planning" "financial adviser" "{county}"',
        ]
        for query in li_queries:
            time.sleep(_DELAY)
            for org in self._linkedin_profiles(query, lat, lon, town):
                _add(org)

        print(f"[solla] Found {len(results)} entries")
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _web_firms(self, query: str, lat: float, lon: float, town: str) -> list[dict]:
        """Find SOLLA firm websites via web search."""
        orgs = []
        for hit in _ddg(query, max_results=6):
            href = hit.get("href", "")
            title = _clean_title(hit.get("title", ""))

            if not title or len(title) < 4 or len(title) > 80:
                continue

            try:
                from urllib.parse import urlparse
                domain = urlparse(href).netloc.lstrip("www.")
            except Exception:
                domain = ""

            if any(domain == nd or domain.endswith("." + nd)
                   for nd in (_NEWS_DOMAINS | _SOLLA_NOISE_DOMAINS)):
                continue

            # Must contain a SOLLA/care fees keyword in title, body, or URL
            body = hit.get("body", "")
            combined = (title + " " + href + " " + body).lower()
            if not any(kw in combined for kw in _SOLLA_KW):
                continue

            # Determine best contact role
            if "solla" in combined:
                role = "SOLLA-accredited Adviser"
                note = "SOLLA member — specialises in care fees planning"
            else:
                role = "Later Life / Care Fees Specialist"
                note = "Specialises in care fees and later life financial planning"

            orgs.append(_make_org(
                title, lat, lon, town, href,
                f"solla::{_url_id(href)}",
                [{"name": "", "role": role, "source_notes": note}],
            ))
        return orgs

    def _linkedin_profiles(self, query: str, lat: float, lon: float,
                            town: str) -> list[dict]:
        """Find named SOLLA advisers on LinkedIn; group by employer firm."""
        employers: dict[str, dict] = {}

        for hit in _ddg(query, max_results=6):
            href = hit.get("href", "")
            if "linkedin.com/in/" not in href:
                continue

            contact = _parse_linkedin_profile(hit.get("title", ""), href)
            if not contact:
                continue

            # Reject care workers
            if _CARE_WORKER_RE.search(contact.get("role", "")):
                continue

            # Reject artefacts from DDG concatenated snippets
            name = contact.get("name", "")
            if not name or "linkedin" in name.lower() or "|" in name or len(name) > 60:
                continue

            # Reject US/international professionals
            title_text = hit.get("title", "") + " " + hit.get("body", "")
            if _US_LOCATION_RE.search(title_text):
                continue

            contact["name"] = _clean_name(name)

            # Tag as SOLLA if not already
            existing = contact.get("source_notes", "")
            if "SOLLA" not in existing:
                contact["source_notes"] = (
                    "SOLLA-accredited care fees adviser (LinkedIn)" if not existing
                    else f"SOLLA — {existing}"
                )

            # Derive employer
            employer = ""
            note = contact.get("source_notes", "")
            if " — " in note:
                employer = note.rsplit(" — ", 1)[1].strip()
            raw_snotes = contact.get("source_notes", "")
            # Also try parsing from original source_notes format "LinkedIn — Firm Name"
            if " — " in raw_snotes and employer in ("", "SOLLA"):
                employer = raw_snotes.split(" — ", 1)[1].strip()

            if (not employer or len(employer) < 3 or "..." in employer
                    or len(employer) > 70 or "linkedin" in employer.lower()
                    or "|" in employer):
                employer = f"SOLLA adviser ({contact['role']}) — {town}"

            if employer not in employers:
                li_q = re.sub(r'\s+', '+', employer)
                li_url = f"https://www.linkedin.com/search/results/companies/?keywords={li_q}"
                employers[employer] = _make_org(
                    employer, lat, lon, town,
                    li_url, f"solla::{_url_id(employer + town)}", []
                )
            employers[employer]["contacts"].append(contact)

        return list(employers.values())
