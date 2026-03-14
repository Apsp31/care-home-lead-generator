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
from .geocoder import haversine_km, geocode_place

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
    "dementia_cafe":    {"dementia", "memory", "alzheimer", "cognitive"},
    "age_uk_branch":    {"age uk", "age concern", "ageuk", "ageconcern"},
    "carers_group":     {"carer", "caring", "carers", "family support"},
    "day_centre":       {"day centre", "day center", "daycentre", "day service"},
    "community_group":  {"community", "befriend", "lunch club", "social club"},
    "domiciliary_care": {"domiciliary", "home care", "homecare", "care at home"},
    "care_referral":    {"placement", "referral", "care adviser", "care finder", "navigator"},
    "senior_club":      {"u3a", "university of the third age", "women's institute",
                         "rotary", "probus", "bowls club", "bowling club"},
    "library":          {"library", "libraries"},
    "post_office":      {"post office"},
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

# Non-UK location indicators — used to reject US/international LinkedIn profiles
_US_LOCATION_RE = re.compile(
    r'\b(United States|USA|Florida|California|New York|Texas|Chicago|'
    r'New Jersey|Massachusetts|Pennsylvania|Georgia|Ohio|'
    r'Canada|Ontario|Toronto|British Columbia|'
    r'Australia|Sydney|Melbourne|'
    r'India|Singapore|Dubai|UAE|'
    r'Ireland(?!\s+(Road|Street|Avenue|Lane|Close|Drive|Way|Place|Row)))\b',
    re.I,
)

# Patterns that embed a place name inside a community org name
_NAMED_PLACE_RES = [
    re.compile(r'^(?:age\s+uk|age\s+concern)\s+(.+)$', re.I),
    re.compile(r'^(.+?)\s+carers?\s+(?:group|centre|hub|support|network|trust)$', re.I),
    re.compile(r'^(.+?)\s+day\s+cent(?:re|er)s?$', re.I),
    re.compile(r'^(.+?)\s+(?:dementia|memory)\s+(?:cafe|café|group|support)$', re.I),
    re.compile(r'^(.+?)\s+(?:befriending|lunch\s+club|social\s+club)$', re.I),
    re.compile(r'^(.+?)\s+(?:library|libraries|public\s+library)$', re.I),
    re.compile(r'^(.+?)\s+post\s+office$', re.I),
    re.compile(r'^(?:u3a|university\s+of\s+the\s+third\s+age)\s+(.+)$', re.I),
    re.compile(r'^(.+?)\s+(?:u3a|rotary\s+club|women\'s\s+institute|probus|bowls?\s+club)$', re.I),
]
_GENERIC_WORDS = {'local', 'online', 'virtual', 'national', 'uk', 'the', 'community'}


def _place_from_name(name: str) -> str | None:
    """Extract the place name embedded in a community org name, e.g. 'Age UK Barnet' → 'Barnet'."""
    for pat in _NAMED_PLACE_RES:
        m = pat.match(name.strip())
        if m:
            place = m.group(1).strip()
            if 3 <= len(place) <= 40 and place.lower() not in _GENERIC_WORDS:
                return place
    return None


def _in_area(place: str, lat: float, lon: float, radius_km: float) -> bool:
    """Return True if the place geocodes within radius_km * 1.5 of (lat, lon), or if unknown."""
    coords = geocode_place(place)
    if coords is None:
        return True  # Can't verify — give benefit of the doubt
    return haversine_km(lat, lon, *coords) <= radius_km * 1.5


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
    ("home care agency elderly",         "domiciliary_care"),
    ("domiciliary care provider",        "domiciliary_care"),
    ("care at home agency",              "domiciliary_care"),
    ("care home placement agency",       "care_referral"),
    ("care placement adviser",           "care_referral"),
    ("care home finder",                 "care_referral"),
    ("care navigator elderly",           "care_referral"),
    ("U3A branch",                       "senior_club"),
    ("University of the Third Age",      "senior_club"),
    ("Women's Institute branch",         "senior_club"),
    ("Rotary club",                      "senior_club"),
    ("Probus club retired",              "senior_club"),
    ("bowls club seniors",               "senior_club"),
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
    ("home care manager domiciliary",            "domiciliary_care"),
    ("care placement coordinator",               "care_referral"),
    ("care adviser care home placement",         "care_referral"),
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
    "domiciliary_care": [
        {"name": "", "role": "Registered Manager",
         "source_notes": "CQC-registered manager; observes client decline and trusted by families"},
        {"name": "", "role": "Care Coordinator",
         "source_notes": "Day-to-day contact; often first to identify when home care is no longer enough"},
    ],
    "care_referral": [
        {"name": "", "role": "Care Placement Adviser",
         "source_notes": "Their job is matching people to care homes — highest-value referral contact"},
        {"name": "", "role": "Care Navigator",
         "source_notes": "Guides families through care options; direct route to placement referrals"},
    ],
    "senior_club": [
        {"name": "", "role": "Chair / Group Organiser",
         "source_notes": "Organises meetings for active older adults — members are at or approaching care planning age"},
        {"name": "", "role": "Secretary",
         "source_notes": "Circulates notices and newsletters to all members; good route for event flyers"},
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
    # Strip everything after | (parent brands, site names, etc.)
    title = re.sub(r'\s*\|.*$', '', title)
    # Strip known navigation labels after – or -
    title = re.sub(
        r'\s*[–\-]\s*(Home|About|Welcome|Events|Contact Us?|News|'
        r'Facebook|LinkedIn|Twitter|Instagram)\s*$',
        '', title, flags=re.I,
    )
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
            key = re.sub(r'\s*\|.*$', '', org["name"])  # strip parent brand suffix
            key = re.sub(r'\s+', ' ', key.lower().strip())
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
            for org in self._facebook_pages(query, org_type, lat, lon, radius_km, town):
                _add(org)

        # ── 3. LinkedIn area searches — named professionals ───────────────────
        for role_phrase, org_type in LINKEDIN_AREA_SEARCHES:
            time.sleep(_DELAY)
            query = f'site:linkedin.com/in "{role_phrase}" "{town}"'
            for org in self._linkedin_profiles(query, org_type, lat, lon, radius_km, town):
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
            else:
                # No postcode — try to verify area from place name embedded in org title
                place = _place_from_name(title)
                if place and place.lower() not in town.lower() and town.lower() not in place.lower():
                    if not _in_area(place, lat, lon, radius_km):
                        continue

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km * 1.5:
                continue

            orgs.append(_make_org(title, org_type, org_lat, org_lon,
                                  dist, org_pc, town, href,
                                  f"web::{_url_id(href)}"))
        return orgs

    def _facebook_pages(self, query: str, org_type: str,
                        lat: float, lon: float, radius_km: float, town: str) -> list[dict]:
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
            postcode = _extract_postcode(hit.get("body", ""))
            org_lat, org_lon, org_pc, dist = lat, lon, "", 0.0
            if postcode:
                coords = _geocode_postcode(postcode)
                if coords:
                    org_lat, org_lon = coords
                    org_pc = postcode
                    dist = round(haversine_km(lat, lon, org_lat, org_lon), 2)
            else:
                place = _place_from_name(title)
                if place and place.lower() not in town.lower() and town.lower() not in place.lower():
                    if not _in_area(place, lat, lon, radius_km):
                        continue
            if dist > radius_km * 1.5:
                continue
            orgs.append(_make_org(title, org_type, org_lat, org_lon, dist,
                                  org_pc, town, href, f"fb::{_url_id(href)}"))
        return orgs

    def _linkedin_profiles(self, query: str, org_type: str,
                           lat: float, lon: float, radius_km: float, town: str) -> list[dict]:
        """Find named individuals; group into org entries keyed by employer."""
        employers: dict[str, dict] = {}
        for hit in _ddg(query, max_results=6):
            href = hit.get("href", "")
            if "linkedin.com/in/" not in href:
                continue
            contact = _parse_linkedin_profile(hit.get("title", ""), href)
            if not contact:
                continue

            # Reject US/international professionals
            title_text = hit.get("title", "") + " " + hit.get("body", "")
            if _US_LOCATION_RE.search(title_text):
                continue

            # Derive employer name from source_notes
            employer = ""
            note = contact.get("source_notes", "")
            if " — " in note:
                employer = note.split(" — ", 1)[1].strip()
            # Reject truncated or personal-name-looking employers
            if not employer or len(employer) < 3 or "..." in employer or len(employer) > 70:
                employer = f"{contact['role']}s near {town}"

            # Verify employer is in the right area (geocode if it looks like a place/org name)
            if employer not in employers:
                place = _place_from_name(employer)
                if place and place.lower() not in town.lower() and town.lower() not in place.lower():
                    if not _in_area(place, lat, lon, radius_km):
                        continue
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
            postcode = _extract_postcode(hit.get("body", ""))
            org_lat, org_lon, org_pc, dist = lat, lon, "", 0.0
            if postcode:
                coords = _geocode_postcode(postcode)
                if coords:
                    org_lat, org_lon = coords
                    org_pc = postcode
                    dist = round(haversine_km(lat, lon, org_lat, org_lon), 2)
            orgs.append(_make_org(title, org_type, org_lat, org_lon, dist,
                                  org_pc, town, href, f"li::{_url_id(href)}"))
        return orgs
