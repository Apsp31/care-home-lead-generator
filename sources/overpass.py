"""OpenStreetMap Overpass API — hospitals (expanded to departments), hospices, pharmacies, community orgs."""
import time
import requests
from .base import DataSource
from .geocoder import haversine_km, postcode_to_latlon


def _latlon_from_element(el: dict, tags: dict, fallback_lat: float,
                          fallback_lon: float) -> tuple[float, float, bool]:
    """Return (lat, lon, is_precise). Falls back to postcode geocoding, then care home coords."""
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if lat is not None and lon is not None:
        return lat, lon, True
    pc = tags.get("addr:postcode", "")
    if pc:
        try:
            lat, lon = postcode_to_latlon(pc)
            return lat, lon, True
        except Exception:
            pass
    return fallback_lat, fallback_lon, False

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# (overpass tag filter, org_type label)
# Hospitals are handled separately — expanded to department leads
NON_HOSPITAL_QUERIES = [
    ('amenity=hospice',           'hospice'),
    ('amenity=pharmacy',          'pharmacy'),
    ('amenity=community_centre',  'community_group'),
    ('amenity=social_facility',   'social_services'),
    ('amenity=place_of_worship',  'place_of_worship'),
    ('office=government;government=social_services', 'social_services'),
    ('amenity=nursing_home',      'nursing_home'),
    ('amenity=retirement_home',   'nursing_home'),
    ('amenity=library',           'library'),
    ('amenity=post_office',       'post_office'),
]

# Departments to generate per hospital — (dept_suffix, org_type, contacts)
HOSPITAL_DEPARTMENTS = [
    (
        "Private Patient Unit",
        "hospital_private",
        [
            {"name": "", "role": "Private Patient Coordinator",
             "source_notes": "Manages post-discharge placement for self-funding patients"},
            {"name": "", "role": "Private Patient Manager",
             "source_notes": "Senior contact for private patient services"},
        ],
    ),
    (
        "Discharge Planning / Transfer of Care Team",
        "hospital_discharge",
        [
            {"name": "", "role": "Discharge Liaison Nurse",
             "source_notes": "Coordinates care placements for medically fit patients"},
            {"name": "", "role": "Transfer of Care Coordinator",
             "source_notes": "Arranges step-down and residential placements"},
        ],
    ),
    (
        "Frailty & Elderly Care Unit",
        "hospital_frailty",
        [
            {"name": "", "role": "Consultant Geriatrician",
             "source_notes": "Lead clinician for elderly care; key influencer for residential placement"},
            {"name": "", "role": "Frailty Nurse Practitioner",
             "source_notes": "Frontline contact managing frailty pathway"},
        ],
    ),
    (
        "Memory Clinic / Dementia Service",
        "hospital_dementia",
        [
            {"name": "", "role": "Old Age Psychiatrist",
             "source_notes": "Consultant leading dementia diagnosis and care planning"},
            {"name": "", "role": "Dementia Specialist Nurse",
             "source_notes": "Nurse specialist supporting families through care transitions"},
        ],
    ),
    (
        "Trauma & Orthopaedics",
        "hospital_ortho",
        [
            {"name": "", "role": "Trauma & Orthopaedic Consultant",
             "source_notes": "Treats hip/knee patients — predominantly 70+ with assets"},
            {"name": "", "role": "Orthopaedic Liaison Nurse",
             "source_notes": "Coordinates post-operative care and step-down placements"},
        ],
    ),
    (
        "Stroke Rehabilitation Unit",
        "hospital_stroke",
        [
            {"name": "", "role": "Stroke Consultant",
             "source_notes": "Lead clinician for stroke pathway; involved in long-term care planning"},
            {"name": "", "role": "Stroke Rehabilitation Coordinator",
             "source_notes": "Manages discharge and ongoing care placement"},
        ],
    ),
    (
        "Social Work Department",
        "hospital_social_work",
        [
            {"name": "", "role": "Principal Hospital Social Worker",
             "source_notes": "Identifies self-funders above £23,250 threshold and arranges placements"},
            {"name": "", "role": "Adult Social Care Coordinator",
             "source_notes": "Frontline case worker for care transitions"},
        ],
    ),
]

CONTACTS_BY_TYPE = {
    "hospice": [
        {"name": "", "role": "Social Worker",
         "source_notes": "Supports families through end-of-life care transitions"},
        {"name": "", "role": "Nurse Manager",
         "source_notes": "Senior clinical lead"},
    ],
    "pharmacy": [
        {"name": "", "role": "Superintendent Pharmacist",
         "source_notes": "Registered manager and community health contact"},
    ],
    "community_group": [
        {"name": "", "role": "Coordinator / Chair",
         "source_notes": "Key contact for community outreach and events"},
    ],
    "social_services": [
        {"name": "", "role": "Adult Social Care Team Manager",
         "source_notes": "Manages statutory assessments and self-funder identification"},
        {"name": "", "role": "Care Navigator",
         "source_notes": "Frontline contact guiding families through options"},
    ],
    "place_of_worship": [
        {"name": "", "role": "Pastoral Lead (Vicar / Imam / Rabbi)",
         "source_notes": "Trusted community figure reaching isolated elderly"},
    ],
    "nursing_home": [
        {"name": "", "role": "Registered Manager",
         "source_notes": "Peer contact for cross-referrals when at capacity"},
    ],
    "library": [
        {"name": "", "role": "Branch Manager / Head of Library Services",
         "source_notes": "Manages community noticeboard and events programme; high footfall from older residents"},
    ],
    "post_office": [
        {"name": "", "role": "Postmaster / Branch Manager",
         "source_notes": "Daily footfall heavily skewed to older adults; community noticeboard and leaflet point"},
    ],
}


class OverpassSource(DataSource):
    name = "overpass"

    # Set to a subset of hospital_* org_type strings to restrict department expansion.
    # None means all 7 departments.
    dept_types: set[str] | None = None

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        radius_m = int(radius_km * 1000)
        results = []
        seen_ids = set()

        # Hospitals — expanded to department leads
        try:
            hospital_leads = self._fetch_hospitals(lat, lon, radius_m, radius_km)
            for org in hospital_leads:
                key = org["source_id"]
                if key not in seen_ids:
                    seen_ids.add(key)
                    results.append(org)
        except Exception as e:
            print(f"[overpass] Error fetching hospitals: {e}")

        # All other amenity types — batched into a single Overpass request
        try:
            other_orgs = self._batch_query(lat, lon, radius_m)
            for org in other_orgs:
                key = org["source_id"]
                if key not in seen_ids:
                    seen_ids.add(key)
                    results.append(org)
        except Exception as e:
            print(f"[overpass] Batch query error: {e}")

        return results

    def _fetch_hospitals(self, lat: float, lon: float,
                         radius_m: int, radius_km: float) -> list[dict]:
        """Fetch hospitals and expand each to multiple department leads."""
        ql = f"""
        [out:json][timeout:25];
        (
          node["amenity"="hospital"](around:{radius_m},{lat},{lon});
          way["amenity"="hospital"](around:{radius_m},{lat},{lon});
        );
        out center tags;
        """
        elements = self._post_with_fallback(ql, timeout=30)

        results = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name", "")
            if not name:
                continue

            el_lat, el_lon, _ = _latlon_from_element(el, tags, lat, lon)
            if el_lat is None:
                continue

            dist = haversine_km(lat, lon, el_lat, el_lon)
            if dist > radius_km:
                continue

            base = {
                "address_line1": tags.get("addr:street", ""),
                "address_line2": "",
                "town": tags.get("addr:city", tags.get("addr:town", "")),
                "postcode": tags.get("addr:postcode", ""),
                "lat": el_lat,
                "lon": el_lon,
                "distance_km": round(dist, 2),
                "phone": tags.get("phone", tags.get("contact:phone", "")),
                "email": tags.get("email", tags.get("contact:email", "")),
                "website": tags.get("website", tags.get("contact:website", "")),
                "source": self.name,
            }

            # Skip private patient unit if hospital is clearly NHS community/specialist
            # (keep all departments for general acute hospitals)
            hospital_type = tags.get("healthcare", "") + tags.get("operator:type", "")
            skip_private = "community" in hospital_type.lower()

            for dept_label, org_type, contacts in HOSPITAL_DEPARTMENTS:
                if org_type == "hospital_private" and skip_private:
                    continue
                if self.dept_types is not None and org_type not in self.dept_types:
                    continue
                dept_id = f"{el['type']}/{el['id']}::{org_type}"
                results.append({
                    **base,
                    "name": f"{name} — {dept_label}",
                    "org_type": org_type,
                    "source_id": dept_id,
                    "contacts": contacts,
                })

        return results

    def _batch_query(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        """Per-type Overpass queries (one request per amenity type) to avoid 504s on large unions."""
        results = []
        seen_ids: set[str] = set()

        for tag_filter, org_type in NON_HOSPITAL_QUERIES:
            time.sleep(1)
            try:
                elements = self._query_single_type(tag_filter, lat, lon, radius_m)
            except Exception as e:
                print(f"[overpass] Query failed for {tag_filter}: {e}")
                continue

            for el in elements:
                el_id = f"{el['type']}/{el['id']}"
                if el_id in seen_ids:
                    continue
                seen_ids.add(el_id)

                tags = el.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue

                el_lat, el_lon, precise = _latlon_from_element(el, tags, lat, lon)
                dist = round(haversine_km(lat, lon, el_lat, el_lon), 2) if precise else 0.0

                results.append({
                    "name": name,
                    "org_type": org_type,
                    "source": self.name,
                    "source_id": el_id,
                    "address_line1": tags.get("addr:street", ""),
                    "address_line2": "",
                    "town": tags.get("addr:city", tags.get("addr:town", "")),
                    "postcode": tags.get("addr:postcode", ""),
                    "lat": el_lat,
                    "lon": el_lon,
                    "distance_km": dist,
                    "phone": tags.get("phone", tags.get("contact:phone", "")),
                    "email": tags.get("email", tags.get("contact:email", "")),
                    "website": tags.get("website", tags.get("contact:website", "")),
                    "contacts": CONTACTS_BY_TYPE.get(org_type, []),
                })

        return results

    def _query_single_type(self, tag_filter: str, lat: float, lon: float,
                           radius_m: int) -> list[dict]:
        """Single Overpass request for one amenity type."""
        tag_parts = tag_filter.split(";")
        tag_conditions = "".join(f'["{p.split("=")[0]}"="{p.split("=")[1]}"]'
                                 for p in tag_parts)
        ql = f"""
        [out:json][timeout:25];
        (
          node{tag_conditions}(around:{radius_m},{lat},{lon});
          way{tag_conditions}(around:{radius_m},{lat},{lon});
        );
        out center tags;
        """
        return self._post_with_fallback(ql, timeout=30)

    def _post_with_fallback(self, ql: str, timeout: int = 30) -> list[dict]:
        """Try each mirror in turn; return elements from the first that succeeds."""
        last_exc = None
        for url in OVERPASS_MIRRORS:
            try:
                resp = requests.post(url, data={"data": ql}, timeout=timeout)
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except Exception as e:
                last_exc = e
                time.sleep(1)
        raise last_exc

