"""Retirement village discovery via targeted web search.

Searches for UK retirement villages near the target location using:
  1. Generic local searches ("retirement village <town>")
  2. Provider-specific searches for the major UK operators
  3. Postcode extraction + geocoding for distance filtering

Key UK providers targeted:
  McCarthy & Stone, Churchill Retirement Living, Inspired Villages,
  Audley Villages, Richmond Villages, Rangeford Villages, Birchgrove,
  ExtraCare / Retirement Villages Group, Pegasus Life, Housing 21, Anchor
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
    _ddg, _url_id, _reverse_geocode_town, _clean_title,
    _NEWS_DOMAINS, _DELAY, _extract_postcode, _geocode_postcode,
)
from .geocoder import haversine_km

# Major UK retirement village / retirement living operators
_PROVIDERS = [
    "McCarthy Stone",
    "Churchill Retirement Living",
    "Inspired Villages",
    "Audley Villages",
    "Richmond Villages",
    "Rangeford Villages",
    "Birchgrove",
    "ExtraCare",
    "Pegasus Life",
    "Housing 21",
    "Anchor retirement",
    "Retirement Villages Group",
    "Homewise",
]

# Domains that are directories / news / info sites — not the village itself
_NOISE_DOMAINS = {
    "rightmove.co.uk", "zoopla.co.uk", "onthemarket.com",
    "carehome.co.uk", "carehomeselect.com", "lottie.org",
    "retirementvillages.net",  # directory
    "which.co.uk", "thisismoney.co.uk", "moneysavingexpert.com",
    "ageuk.org.uk", "citizensadvice.org.uk",
    "retirementmoves.co.uk",  # property broker
    "laterlivingnow.co.uk",   # directory
    "housingcare.org",
    "theguardian.com", "bbc.co.uk", "telegraph.co.uk", "independent.co.uk",
}

# Keywords that confirm a result is a retirement village (not a care home)
_VILLAGE_KW = {
    "retirement village", "retirement living", "retirement community",
    "retirement development", "retirement apartment", "retirement bungalow",
    "later living", "independent living", "retirement home",
}

# Keywords that indicate a standard care / nursing home (exclude these)
_CARE_HOME_KW = {
    "nursing home", "residential care", "dementia care home",
    "care home", "registered care",
}


def _is_village(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in _CARE_HOME_KW):
        return False
    return any(kw in t for kw in _VILLAGE_KW)


def _make_org(name: str, lat: float, lon: float, town: str,
              postcode: str, website: str, source_id: str,
              provider: str, contacts: list[dict]) -> dict:
    return {
        "name": name,
        "org_type": "retirement_village",
        "source": "retirement_villages",
        "source_id": source_id,
        "address_line1": "",
        "address_line2": "",
        "town": town,
        "postcode": postcode,
        "lat": lat,
        "lon": lon,
        "distance_km": 0.0,
        "phone": "",
        "email": "",
        "website": website,
        "contacts": contacts,
        "_provider": provider,
    }


class RetirementVillagesSource(DataSource):
    name = "retirement_villages"

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if not _DDG_AVAILABLE:
            print("[retirement_villages] ddgs not installed. Run: pip install ddgs")
            return []

        town = _reverse_geocode_town(lat, lon)
        if not town:
            print("[retirement_villages] Could not determine town from coordinates.")
            return []

        print(f"[retirement_villages] Searching retirement villages near: {town}")
        results: list[dict] = []
        seen: set[str] = set()

        def _add(org: dict):
            key = re.sub(r'\s+', ' ', org["name"].lower().strip())
            if key and key not in seen and len(key) > 3:
                seen.add(key)
                results.append(org)

        # ── 1. Generic local searches ─────────────────────────────────────────
        generic_queries = [
            f'"retirement village" "{town}"',
            f'"retirement living" "{town}"',
            f'"retirement community" "{town}"',
            f'"later living" "{town}"',
            f'"independent living" "retirement" "{town}"',
        ]
        for query in generic_queries:
            time.sleep(_DELAY)
            for org in self._search_villages(query, lat, lon, radius_km, town, ""):
                _add(org)

        # ── 2. Provider-specific searches ─────────────────────────────────────
        for provider in _PROVIDERS:
            query = f'"{provider}" "{town}"'
            time.sleep(_DELAY)
            for org in self._search_villages(query, lat, lon, radius_km, town, provider):
                _add(org)

        print(f"[retirement_villages] Found {len(results)} entries")
        return results

    def _search_villages(self, query: str, lat: float, lon: float,
                         radius_km: float, town: str, provider: str) -> list[dict]:
        orgs = []
        for hit in _ddg(query, max_results=5):
            href = hit.get("href", "")
            title = _clean_title(hit.get("title", ""))
            body = hit.get("body", "")

            if not title or len(title) < 4 or len(title) > 100:
                continue

            try:
                from urllib.parse import urlparse
                domain = urlparse(href).netloc.lstrip("www.")
            except Exception:
                domain = ""

            if any(domain == nd or domain.endswith("." + nd)
                   for nd in (_NEWS_DOMAINS | _NOISE_DOMAINS)):
                continue

            combined = (title + " " + body + " " + href).lower()

            # Must match a village keyword; skip pure care homes
            if not _is_village(combined):
                # Accept if provider name is in the URL/title even without keyword
                if not provider or provider.split()[0].lower() not in combined:
                    continue

            # Geocode from postcode in snippet
            postcode = _extract_postcode(body)
            org_lat, org_lon = lat, lon
            if postcode:
                coords = _geocode_postcode(postcode)
                if coords:
                    org_lat, org_lon = coords

            # Radius guard
            dist = haversine_km(lat, lon, org_lat, org_lon)
            if org_lat != lat and dist > radius_km * 1.5:
                continue

            # Infer provider from URL/title if not supplied
            inferred_provider = provider
            if not inferred_provider:
                for p in _PROVIDERS:
                    if p.lower().split()[0] in combined:
                        inferred_provider = p
                        break

            role = "Village Manager"
            note = f"Retirement village{' — ' + inferred_provider if inferred_provider else ''}"

            org = _make_org(
                title, org_lat, org_lon, town, postcode or "", href,
                f"retirement_villages::{_url_id(href)}",
                inferred_provider,
                [{"name": "", "role": role, "source_notes": note}],
            )
            if org_lat != lat or org_lon != lon:
                org["distance_km"] = round(dist, 2)
            orgs.append(org)
        return orgs
