"""CQC Registered Providers — homecare agencies and community social care.

Uses CQC public API v1 (no authentication required).
Strategy:
  1. Resolve local authority from coordinates via postcodes.io
  2. Fetch all non-residential registered locations in that LA
  3. Geocode each by postcode; haversine distance filter
  4. Fetch provider detail for registered manager name (cached per provider)
"""
import re
import time
import requests
from .base import DataSource
from .geocoder import haversine_km, postcode_to_latlon

_CQC_BASE = "https://api.cqc.org.uk/public/v1"
_DELAY = 0.25  # seconds between provider detail calls

# CQC gacServiceType name → our org_type; None = skip
_SERVICE_TYPE_MAP = {
    "Homecare agencies":                                          "domiciliary_care",
    "Supported living services":                                  "domiciliary_care",
    "Extra Care housing services":                                "domiciliary_care",
    "Community based services for older people":                  "social_services",
    "Community based services for people with mental health needs": "social_services",
    "Community based services for people with a learning disability": None,  # skip
    "Shared lives":                                               None,  # skip
    "Residential social care":                                    None,  # skip — low value, already in OSM
    "With or Without Nursing":                                    None,  # skip (care home sub-type)
}

_ROLE_PLACEHOLDERS: dict[str, list[dict]] = {
    "domiciliary_care": [
        {"name": "", "role": "Registered Manager",
         "source_notes": "CQC-registered homecare provider; observes client decline daily"},
    ],
    "social_services": [
        {"name": "", "role": "Service Manager",
         "source_notes": "CQC-registered community social care provider"},
    ],
}


def _local_authority(lat: float, lon: float) -> str:
    try:
        resp = requests.get(
            "https://api.postcodes.io/postcodes",
            params={"lon": lon, "lat": lat, "limit": 1},
            timeout=8,
        )
        if resp.status_code == 200:
            results = resp.json().get("result") or []
            if results:
                return results[0].get("admin_district", "")
    except Exception:
        pass
    return ""


def _map_type(location: dict) -> str | None:
    """Return our org_type for the first matching CQC service type, or None."""
    service_types = location.get("gacServiceTypes", [])
    for st in service_types:
        mapped = _SERVICE_TYPE_MAP.get(st.get("name", "") if isinstance(st, dict) else str(st))
        if mapped is not None:
            return mapped
    # Fallback: non-residential location with no mapped type → assume homecare
    if not service_types:
        return "domiciliary_care"
    return None


def _fetch_provider(provider_id: str) -> dict:
    try:
        resp = requests.get(f"{_CQC_BASE}/providers/{provider_id}", timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def _extract_contacts(provider: dict, org_type: str) -> list[dict]:
    contacts = []
    for contact in provider.get("contacts", []):
        roles = [
            r.get("name", "") if isinstance(r, dict) else str(r)
            for r in contact.get("roles", [])
        ]
        if not any(r in {"Registered Manager", "Nominated Individual"} for r in roles):
            continue
        parts = [
            contact.get("title", ""),
            contact.get("givenName", ""),
            contact.get("familyName", ""),
        ]
        name = " ".join(p for p in parts if p).strip()
        contacts.append({
            "name": name,
            "role": roles[0] if roles else "Registered Manager",
            "email": "",
            "phone": "",
            "source_notes": "CQC registered contact",
        })
    return contacts


class CQCSource(DataSource):
    name = "cqc"

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        la = _local_authority(lat, lon)
        if not la:
            print("[cqc] Could not determine local authority.")
            return []

        print(f"[cqc] Querying non-residential providers in: {la}")
        try:
            resp = requests.get(
                f"{_CQC_BASE}/locations",
                params={
                    "localAuthority": la,
                    "registrationStatus": "Registered",
                    "careHome": "N",
                    "perPage": 1000,
                },
                timeout=30,
            )
        except Exception as e:
            print(f"[cqc] API error: {e}")
            return []

        if resp.status_code != 200:
            print(f"[cqc] HTTP {resp.status_code}")
            return []

        locations = resp.json().get("locations", [])
        results = []
        provider_cache: dict[str, dict] = {}

        for loc in locations:
            org_type = _map_type(loc)
            if not org_type:
                continue

            postcode = loc.get("postalCode", "")
            if not postcode:
                continue

            try:
                org_lat, org_lon = postcode_to_latlon(postcode)
            except Exception:
                continue

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km:
                continue

            # Fetch provider detail for registered manager (cached per provider)
            provider_id = loc.get("providerId", "")
            contacts = []
            if provider_id:
                if provider_id not in provider_cache:
                    time.sleep(_DELAY)
                    provider_cache[provider_id] = _fetch_provider(provider_id)
                contacts = _extract_contacts(provider_cache[provider_id], org_type)
            if not contacts:
                contacts = list(_ROLE_PLACEHOLDERS.get(org_type, []))

            addr = loc.get("address") or {}
            if not isinstance(addr, dict):
                addr = {}

            results.append({
                "name": loc.get("name", ""),
                "org_type": org_type,
                "source": "cqc",
                "source_id": f"cqc::{loc.get('locationId', '')}",
                "address_line1": addr.get("addressLine1", ""),
                "address_line2": addr.get("addressLine2", ""),
                "town": addr.get("townOrCity", ""),
                "postcode": postcode,
                "lat": org_lat,
                "lon": org_lon,
                "distance_km": round(dist, 2),
                "phone": loc.get("phone", ""),
                "email": "",
                "website": loc.get("website", ""),
                "contacts": contacts,
            })

        print(f"[cqc] Found {len(results)} providers within {radius_km} km")
        return results
