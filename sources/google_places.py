"""Google Places API (New) — local businesses: solicitors, IFAs, estate agents, pharmacies.

Requires GOOGLE_PLACES_API_KEY in .env. Skips gracefully if not set.
Uses the Places API v1 Nearby Search endpoint (POST).

Note: Google Places charges per request. Each search type = 1 billable call.
"""
import os
import re
import requests
from .base import DataSource
from .geocoder import haversine_km

_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"

_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.location,places.nationalPhoneNumber,places.websiteUri"
)

# Google Places included type → (our org_type, contacts)
_TYPE_CONFIG: dict[str, dict] = {
    "lawyer": {
        "org_type": "solicitor",
        "contacts": [
            {"name": "", "role": "Senior Partner — Private Client / Wills & LPA",
             "source_notes": "Google Places — solicitor; target wills, probate and PoA work"},
        ],
    },
    "financial_planner": {
        "org_type": "financial_adviser",
        "contacts": [
            {"name": "", "role": "Independent Financial Adviser (Care Fees Planning)",
             "source_notes": "Google Places — IFA"},
        ],
    },
    "insurance_agency": {
        "org_type": "financial_adviser",
        "contacts": [
            {"name": "", "role": "Financial Adviser",
             "source_notes": "Google Places — financial services"},
        ],
    },
    "real_estate_agency": {
        "org_type": "estate_agent",
        "contacts": [
            {"name": "", "role": "Branch Manager / Later Living Specialist",
             "source_notes": "Google Places — estate agent; target downsizing / probate sales"},
        ],
    },
    "pharmacy": {
        "org_type": "pharmacy",
        "contacts": [
            {"name": "", "role": "Superintendent Pharmacist",
             "source_notes": "Google Places — community pharmacy"},
        ],
    },
}

_PC_RE = re.compile(r'\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b', re.I)


def _postcode(address: str) -> str:
    m = _PC_RE.search(address)
    return m.group(0).upper() if m else ""


class GooglePlacesSource(DataSource):
    name = "google_places"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if not self.api_key:
            print("[google_places] No API key — set GOOGLE_PLACES_API_KEY in .env. Skipping.")
            return []

        results = []
        seen: set[str] = set()

        for gtype, config in _TYPE_CONFIG.items():
            try:
                for org in self._nearby_search(gtype, config, lat, lon, radius_km):
                    if org["source_id"] not in seen:
                        seen.add(org["source_id"])
                        results.append(org)
            except Exception as e:
                print(f"[google_places] Error for type {gtype}: {e}")

        print(f"[google_places] Found {len(results)} places")
        return results

    def _nearby_search(self, gtype: str, config: dict,
                       lat: float, lon: float, radius_km: float) -> list[dict]:
        resp = requests.post(
            _PLACES_URL,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": _FIELD_MASK,
                "Content-Type": "application/json",
            },
            json={
                "includedTypes": [gtype],
                "maxResultCount": 20,
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": float(min(radius_km * 1000, 50000)),
                    }
                },
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[google_places] HTTP {resp.status_code} for {gtype}: {resp.text[:200]}")
            return []

        results = []
        for place in resp.json().get("places", []):
            location = place.get("location", {})
            plat = location.get("latitude")
            plon = location.get("longitude")
            if plat is None or plon is None:
                continue

            dist = haversine_km(lat, lon, plat, plon)
            if dist > radius_km:
                continue

            name = place.get("displayName", {}).get("text", "")
            if not name:
                continue

            address = place.get("formattedAddress", "")
            parts = [p.strip() for p in address.split(",")]
            addr1 = parts[0] if parts else ""
            town = parts[-3].strip() if len(parts) >= 3 else (parts[-2].strip() if len(parts) >= 2 else "")

            results.append({
                "name": name,
                "org_type": config["org_type"],
                "source": "google_places",
                "source_id": f"gp::{place.get('id', '')}",
                "address_line1": addr1,
                "address_line2": "",
                "town": town,
                "postcode": _postcode(address),
                "lat": plat,
                "lon": plon,
                "distance_km": round(dist, 2),
                "phone": place.get("nationalPhoneNumber", ""),
                "email": "",
                "website": place.get("websiteUri", ""),
                "contacts": list(config["contacts"]),
            })

        return results
