"""Web / Social search source — discovers organisations and named contacts via DuckDuckGo.

Three search strategies:
  1. Community org discovery (dementia cafes, memory cafes, Age UK branches, carers groups,
     day centres) — general web + Facebook pages.
  2. LinkedIn area searches for named professionals (practice managers, discharge liaisons,
     solicitors, IFAs, dementia coordinators) — grouped by employer.
  3. LinkedIn company page discovery for care-adjacent orgs.

News articles, press releases, and non-org pages are filtered out.
"""
import re
import time
import hashlib
import requests
from .base import DataSource
from .geocoder import haversine_km

try:
    from ddgs import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDG_AVAILABLE = True
    except ImportError:
        _DDG_AVAILABLE = False

UK_POSTCODE_RE = re.compile(r'\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b', re.IGNORECASE)

# Domains that are news/directory/aggregator sites — not org homepages
_NEWS_DOMAINS = {
    "bbc.co.uk", "bbc.com", "theguardian.com", "dailymail.co.uk", "mirror.co.uk",
    "telegraph.co.uk", "independent.co.uk", "skysports.com", "sky.com",
    "watfordobserver.co.uk", "hertsadvertiser.co.uk", "hertsmercury.co.uk",
    "eppingforestguardian.co.uk", "thecareruk.com", "carehome.co.uk",
    "yell.com", "bing.com", "google.com", "wikipedia.org", "tiktok.com",
    "twitter.com", "instagram.com", "youtube.com",
    "gov.uk", "nhs.uk",
    # Aggregators / directories that list orgs but aren't the org itself
    "askbart.org", "communities1st.org.uk", "hertscommunitynews.co.uk",
    "raring2go.co.uk", "nearestto.com", "hotfrog.co.uk", "192.com",
    "thomsonlocal.com", "scoot.co.uk", "cylex.co.uk", "freeindex.co.uk",
    "goodsearch.co.uk", "charitychoice.co.uk", "charitybase.uk",
}

# Required keywords per org type — the org name/URL must contain at least one
_TYPE_KEYWORDS: dict[str, set[str]] = {
    "dementia_cafe":  {"dementia", "memory", "alzheimer", "cognitive"},
    "age_uk_branch":  {"age uk", "age concern", "ageuk", "ageconcern"},
    "carers_group":   {"carer", "caring", "carers", "family support"},
    "day_centre":     {"day centre", "day center", "daycentre", "day service"},
    "community_group":{"community", "befriend", "lunch club", "social club"},
}

# URL path patterns that indicate an article/news page rather than an org homepage
_ARTICLE_PATH_RE = re.compile(
    r'/news/|/article/|/blog/|/post/|/story/|/press-release/|/videos?/|'
    r'/events?/|/archive/|/discover/|\d{4}/\d{2}/\d{2}',
    re.I
)

# Title patterns that look like article headlines rather than org names
_HEADLINE_RE = re.compile(
    r'\b(to shine|holding|archive|announces?|launches?|opens?|'
    r'spotlight|news|update|latest|weekly|monthly|annual|'
    r'at karuna|inside aston|neil gaiman)\b',
    re.I
)

# (search phrase, org_type)
COMMUNITY_SEARCHES = [
    ("dementia cafe",                    "dementia_cafe"),
    ("memory cafe",                      "dementia_cafe"),
    ("Alzheimer's Society local group",  "dementia_cafe"),
    ("dementia support group",           "dementia_cafe"),
    ("Age UK branch",                    "age_uk_branch"),
    ("Age Concern local",                "age_uk_branch"),
    ("carers support group elderly",     "carers_group"),
    ("carers centre",                    "carers_group"),
    ("elderly day centre",               "day_centre"),
    ("older people day centre",          "day_centre"),
    ("senior lunch club",                "community_group"),
    ("befriending service older people", "community_group"),
]

FACEBOOK_SEARCHES = [
    ("dementia cafe",          "dementia_cafe"),
    ("memory cafe",            "dementia_cafe"),
    ("Age UK",                 "age_uk_branch"),
    ("carers support group",   "carers_group"),
    ("elderly day centre",     "day_centre"),
]

# LinkedIn area searches — find named individuals, group by employer
LINKEDIN_AREA_SEARCHES = [
    ("practice manager GP surgery",              "GP"),
    ("discharge liaison nurse hospital",         "hospital_discharge"),
    ("complex discharge nurse NHS",              "hospital_discharge"),
    ("discharge facilitator NHS hospital",       "hospital_discharge"),
    ("discharge and flow coordinator NHS",       "hospital_discharge"),
    ("continuing healthcare coordinator NHS",    "hospital_chc"),
    ("continuing healthcare nurse assessor",     "hospital_chc"),
    ("occupational therapist discharge NHS",     "hospital_ot_discharge"),
    ("hospital social worker discharge",         "hospital_social_work"),
    ("private client solicitor wills probate",   "solicitor"),
    ("independent financial adviser care fees",  "financial_adviser"),
    ("SOLLA accredited financial adviser",       "financial_adviser"),
    ("dementia cafe coordinator",                "dementia_cafe"),
    ("age uk manager",                           "age_uk_branch"),
    ("carers support coordinator",               "carers_group"),
]

LINKEDIN_COMPANY_SEARCHES = [
    ('site:linkedin.com/company "dementia"',   "dementia_cafe"),
    ('site:linkedin.com/company "carers"',     "carers_group"),
    ('site:linkedin.com/company "age uk"',     "age_uk_branch"),
    ('site:linkedin.com/company "day centre"', "day_centre"),
]

CONTACTS_BY_TYPE = {
    "dementia_cafe": [
        {"name": "", "role": "Group Coordinator",
         "source_notes": "Runs the session; families attending are often at the care decision point"},
        {"name": "", "role": "Volunteer Lead",
         "source_notes": "Key community connector; trusted by families and carers"},
    ],
    "age_uk_branch": [
        {"name": "", "role": "Branch Manager",
         "source_notes": "Age UK is a trusted advisor; directly signposts families to care options"},
        {"name": "", "role": "Information & Advice Officer",
         "source_notes": "Frontline contact advising families on care funding and placement"},
    ],
    "carers_group": [
        {"name": "", "role": "Carers Support Coordinator",
         "source_notes": "Supports family carers at crisis point — high referral conversion"},
    ],
    "day_centre": [
        {"name": "", "role": "Centre Manager",
         "source_notes": "Observes clients' decline; trusted by families for 'next step' advice"},
    ],
}

_DELAY = 2.0  # seconds between DDG requests


def _ddg(query: str, max_results: int = 5) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        # Suppress "No results found" noise; print real errors
        if "No results" not in str(e):
            print(f"[web_search] DDG error: {e}")
        return []


def _reverse_geocode_town(lat: float, lon: float) -> str:
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
                return r.get("admin_district") or r.get("parish") or ""
    except Exception:
        pass
    return ""


def _geocode_postcode(postcode: str) -> tuple[float, float] | None:
    try:
        from .geocoder import postcode_to_latlon
        return postcode_to_latlon(postcode)
    except Exception:
        return None


def _extract_postcode(text: str) -> str | None:
    m = UK_POSTCODE_RE.search(text)
    return m.group(0).upper() if m else None


def _url_id(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:16]


def _is_org_page(title: str, href: str, org_type: str = "") -> bool:
    """Return True if this result looks like an actual org's page, not a news article."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(href).netloc.lstrip("www.")
    except Exception:
        domain = ""

    # Known news/directory domains → skip
    if any(domain == nd or domain.endswith("." + nd) for nd in _NEWS_DOMAINS):
        return False

    # Article-style URL paths → skip
    if _ARTICLE_PATH_RE.search(href):
        return False

    # Headline-style title → skip
    if _HEADLINE_RE.search(title):
        return False

    # Title too long to be an org name
    if len(title) > 80:
        return False

    # Relevance check: org name must contain a keyword for this org type
    if org_type and org_type in _TYPE_KEYWORDS:
        combined = (title + " " + href).lower()
        if not any(kw in combined for kw in _TYPE_KEYWORDS[org_type]):
            return False

    return True


def _clean_title(title: str) -> str:
    """Strip trailing site suffixes from a page title to get the org name."""
    title = re.sub(r'\s*[|\-–]\s*(Home|About|Welcome|Events|Contact Us?|News|'
                   r'Facebook|LinkedIn|Twitter|Instagram)\s*$', '', title, flags=re.I)
    title = re.sub(r'\s*\|\s*Facebook\s*$', '', title, flags=re.I)
    title = re.sub(r'\s*[|\-–]\s*LinkedIn\s*$', '', title, flags=re.I)
    return title.strip()


def _parse_linkedin_profile(title: str, href: str) -> dict | None:
    """
    Parse a LinkedIn profile page title into a contact dict.
    Handles:
      "Jane Smith - Practice Manager at Oak Tree Surgery | LinkedIn"
      "Jane Smith - Practice Manager - Oak Tree Surgery | LinkedIn"
      "Jane Smith -PracticeManager-Surgery | LinkedIn"  (DDG compact)
    """
    clean = re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=re.I).strip()
    # Split on " - " (spaced) OR " -UpperCase" (compact), preserving hyphenated surnames
    parts = re.split(r' - | -(?=[A-Z])', clean)
    parts = [p.strip().rstrip('-').strip() for p in parts if p.strip()]
    if len(parts) < 2 or not parts[0] or parts[0].lower().startswith(("http", "www")):
        return None

    name = parts[0]
    role = parts[1]
    company = ""

    # "Role at Company" inside the role segment
    if re.search(r'\s+at\s+', role, re.I):
        role, company = re.split(r'\s+at\s+', role, maxsplit=1, flags=re.I)
    elif len(parts) > 2:
        company = parts[2]

    return {
        "name": name.strip(),
        "role": role.strip(),
        "source_notes": (f"LinkedIn — {company.strip()}" if company else "LinkedIn profile"),
    }


def _make_org(name: str, org_type: str, lat: float, lon: float, dist: float,
              postcode: str, town: str, website: str, source_id: str) -> dict:
    return {
        "name": name,
        "org_type": org_type,
        "source": "web_search",
        "source_id": source_id,
        "address_line1": "",
        "address_line2": "",
        "town": town,
        "postcode": postcode,
        "lat": lat,
        "lon": lon,
        "distance_km": round(dist, 2),
        "phone": "",
        "email": "",
        "website": website,
        "contacts": list(CONTACTS_BY_TYPE.get(org_type, [])),
    }


class WebSearchSource(DataSource):
    name = "web_search"

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if not _DDG_AVAILABLE:
            print("[web_search] ddgs not installed. Run: pip install ddgs")
            return []

        town = _reverse_geocode_town(lat, lon)
        if not town:
            print("[web_search] Could not determine town from coordinates.")
            return []

        print(f"[web_search] Searching in: {town}")
        results: list[dict] = []
        seen: set[str] = set()

        def _add(org: dict):
            key = re.sub(r'\s+', ' ', org["name"].lower().strip())
            if key and key not in seen and len(key) > 3:
                seen.add(key)
                results.append(org)

        # ── 1. Community org discovery (general web) ──────────────────────────
        for phrase, org_type in COMMUNITY_SEARCHES:
            time.sleep(_DELAY)
            query = f'"{phrase}" "{town}"'
            for org in self._web_orgs(query, org_type, lat, lon, radius_km, town):
                _add(org)

        # ── 2. Facebook page discovery ────────────────────────────────────────
        for phrase, org_type in FACEBOOK_SEARCHES:
            time.sleep(_DELAY)
            query = f'site:facebook.com "{phrase}" "{town}"'
            for org in self._facebook_pages(query, org_type, lat, lon, town):
                _add(org)

        # ── 3. LinkedIn area searches — named professionals ───────────────────
        for role_phrase, org_type in LINKEDIN_AREA_SEARCHES:
            time.sleep(_DELAY)
            query = f'site:linkedin.com/in "{role_phrase}" "{town}"'
            for org in self._linkedin_profiles(query, org_type, lat, lon, town):
                _add(org)

        # ── 4. LinkedIn company pages ─────────────────────────────────────────
        for query_base, org_type in LINKEDIN_COMPANY_SEARCHES:
            time.sleep(_DELAY)
            query = f'{query_base} "{town}"'
            for org in self._linkedin_companies(query, org_type, lat, lon, town):
                _add(org)

        print(f"[web_search] Found {len(results)} organisations")
        return results

    # ── Search methods ────────────────────────────────────────────────────────

    def _web_orgs(self, query: str, org_type: str,
                  lat: float, lon: float, radius_km: float, town: str) -> list[dict]:
        orgs = []
        for hit in _ddg(query, max_results=5):
            href = hit.get("href", "")
            title = _clean_title(hit.get("title", ""))
            if not _is_org_page(title, href, org_type):
                continue
            if not title or len(title) < 4:
                continue

            postcode = _extract_postcode(hit.get("body", "")) or _extract_postcode(title)
            org_lat, org_lon, org_pc = lat, lon, ""
            if postcode:
                coords = _geocode_postcode(postcode)
                if coords:
                    org_lat, org_lon = coords
                    org_pc = postcode

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km * 1.5:
                continue

            orgs.append(_make_org(title, org_type, org_lat, org_lon,
                                  dist, org_pc, town, href,
                                  f"web::{_url_id(href)}"))
        return orgs

    def _facebook_pages(self, query: str, org_type: str,
                        lat: float, lon: float, town: str) -> list[dict]:
        orgs = []
        for hit in _ddg(query, max_results=4):
            href = hit.get("href", "")
            if "facebook.com" not in href:
                continue
            # Only proper pages, not events/videos/posts/groups
            path = href.split("facebook.com/")[-1].split("?")[0].rstrip("/")
            if any(seg in path for seg in ("events/", "videos/", "posts/",
                                           "watch/", "groups/", "stories/", "photos/")):
                continue
            title = _clean_title(hit.get("title", ""))
            if not title or len(title) < 4 or _HEADLINE_RE.search(title):
                continue
            if not _is_org_page(title, href, org_type):
                continue
            orgs.append(_make_org(title, org_type, lat, lon, 0.0,
                                  "", town, href, f"fb::{_url_id(href)}"))
        return orgs

    def _linkedin_profiles(self, query: str, org_type: str,
                           lat: float, lon: float, town: str) -> list[dict]:
        """Find named individuals; group into org entries keyed by employer."""
        employers: dict[str, dict] = {}
        for hit in _ddg(query, max_results=6):
            href = hit.get("href", "")
            if "linkedin.com/in/" not in href:
                continue
            contact = _parse_linkedin_profile(hit.get("title", ""), href)
            if not contact:
                continue

            # Derive employer name from source_notes
            employer = ""
            note = contact.get("source_notes", "")
            if " — " in note:
                employer = note.split(" — ", 1)[1].strip()
            # Reject truncated or personal-name-looking employers
            if not employer or len(employer) < 3 or "..." in employer or len(employer) > 70:
                employer = f"{contact['role']}s near {town}"

            if employer not in employers:
                li_url = (f"https://www.linkedin.com/search/results/companies/"
                          f"?keywords={re.sub(chr(32), '+', employer)}")
                employers[employer] = _make_org(
                    employer, org_type, lat, lon, 0.0, "", town,
                    li_url, f"li::{_url_id(employer + town)}"
                )
                employers[employer]["contacts"] = []

            employers[employer]["contacts"].append(contact)

        return list(employers.values())

    def _linkedin_companies(self, query: str, org_type: str,
                            lat: float, lon: float, town: str) -> list[dict]:
        orgs = []
        for hit in _ddg(query, max_results=4):
            href = hit.get("href", "")
            if "linkedin.com/company" not in href:
                continue
            title = _clean_title(hit.get("title", ""))
            if not title or len(title) < 4:
                continue
            if not _is_org_page(title, href, org_type):
                continue
            orgs.append(_make_org(title, org_type, lat, lon, 0.0,
                                  "", town, href, f"li::{_url_id(href)}"))
        return orgs
