"""NHS ODS ORD API — GP practices, NHS trusts, PCNs."""
import requests
from .base import DataSource
from .geocoder import haversine_km, bulk_geocode_postcodes

# ODS role codes
ROLE_CODES = {
    "RO177": "GP",           # GP practices
    "RO197": "hospital",     # NHS trusts / acute
    "RO213": "PCN",          # Primary Care Networks
}

BASE_URL = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
_POSTCODES_IO = "https://api.postcodes.io"


def _nearby_outcodes(lat: float, lon: float, radius_km: float) -> list[str]:
    """Return outward codes (e.g. 'WD23', 'HA6') within radius_km of lat/lon."""
    try:
        # Get the local outward code
        resp = requests.get(f"{_POSTCODES_IO}/outcodes",
                            params={"lat": lat, "lon": lon}, timeout=10)
        if resp.status_code != 200:
            return []
        results = resp.json().get("result") or []
        if not results:
            return []
        outcode = results[0]["outcode"]
        # Get nearby outward codes within radius
        r2 = requests.get(f"{_POSTCODES_IO}/outcodes/{outcode}/nearest",
                          params={"limit": 40, "radius": int(radius_km * 1000)},
                          timeout=10)
        if r2.status_code != 200:
            return [outcode]
        return [r["outcode"] for r in (r2.json().get("result") or [])]
    except Exception:
        return []


_bulk_geocode = bulk_geocode_postcodes  # local alias


class NHSODSSource(DataSource):
    name = "nhs_ods"

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        results = []
        for role_code, org_type in ROLE_CODES.items():
            try:
                orgs = self._fetch_role(role_code, org_type, lat, lon, radius_km)
                results.extend(orgs)
            except Exception as e:
                print(f"[nhs_ods] Error fetching {role_code}: {e}")
        return results

    def _fetch_role(self, role_code: str, org_type: str,
                   lat: float, lon: float, radius_km: float) -> list[dict]:
        if org_type == "GP":
            organisations = self._list_gps_by_outcodes(lat, lon, radius_km)
        else:
            resp = requests.get(BASE_URL,
                                params={"PrimaryRoleId": role_code, "Status": "Active",
                                        "Limit": 1000},
                                headers={"Accept": "application/json"}, timeout=15)
            resp.raise_for_status()
            organisations = resp.json().get("Organisations", [])

        # Batch-geocode all postcodes in a few requests instead of one per org
        postcodes = [
            o.get("PostCode", "").replace(" ", "").upper()
            for o in organisations
            if o.get("PostCode")
        ]
        geo_cache = _bulk_geocode(postcodes)

        results = []
        for org in organisations:
            list_pc = org.get("PostCode", "").replace(" ", "").upper()
            coords = geo_cache.get(list_pc)
            if not coords:
                continue
            org_lat, org_lon = coords
            if org_lat is None or org_lon is None:
                continue

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km:
                continue

            # Only fetch full detail for in-range orgs
            try:
                detail = self._fetch_detail(org["OrgId"])
            except Exception:
                continue

            # ODS stores address in GeoLoc.Location; Addresses array is often null
            geo_loc = detail.get("GeoLoc", {}).get("Location", {}) or {}
            addr_list = detail.get("Addresses") or []
            addr = addr_list[0] if addr_list else geo_loc

            contacts = []
            if org_type == "GP":
                contacts = [
                    {"name": "", "role": "Practice Manager", "source_notes": "Role placeholder"},
                    {"name": "", "role": "GP Partner", "source_notes": "Role placeholder"},
                ]
            elif org_type == "hospital":
                contacts = [
                    {"name": "", "role": "Discharge Liaison Nurse", "source_notes": "Role placeholder"},
                    {"name": "", "role": "Social Work Team Lead", "source_notes": "Role placeholder"},
                ]
            elif org_type == "PCN":
                contacts = [
                    {"name": "", "role": "PCN Clinical Director", "source_notes": "Role placeholder"},
                ]

            raw_contacts = (detail.get("Contacts") or {}).get("Contact", [])
            phone   = next((c["value"] for c in raw_contacts if c.get("type") == "tel"), "")
            website = next((c["value"] for c in raw_contacts if c.get("type") == "http"), "")
            email   = next((c["value"] for c in raw_contacts if c.get("type") == "email"), "")

            results.append({
                "name": detail.get("Name", org.get("Name", "")),
                "org_type": org_type,
                "source": self.name,
                "source_id": org["OrgId"],
                "address_line1": addr.get("AddrLn1", ""),
                "address_line2": addr.get("AddrLn2", ""),
                "town": addr.get("Town", ""),
                "postcode": addr.get("PostCode", list_pc),
                "lat": org_lat,
                "lon": org_lon,
                "distance_km": round(dist, 2),
                "phone": phone,
                "email": email,
                "website": website,
                "contacts": contacts,
            })

        return results

    def _list_gps_by_outcodes(self, lat: float, lon: float,
                               radius_km: float) -> list[dict]:
        """Fetch GPs by querying ODS once per nearby outward code — much faster
        than paginating through all 12 000+ RO177 prescribing cost centres."""
        outcodes = _nearby_outcodes(lat, lon, radius_km)
        if not outcodes:
            # Fallback: single page of 1000 from full list
            resp = requests.get(BASE_URL,
                                params={"PrimaryRoleId": "RO177", "Status": "Active",
                                        "Limit": 1000},
                                headers={"Accept": "application/json"}, timeout=15)
            resp.raise_for_status()
            return resp.json().get("Organisations", [])

        seen: set[str] = set()
        organisations: list[dict] = []
        for outcode in outcodes:
            try:
                resp = requests.get(BASE_URL,
                                    params={"PrimaryRoleId": "RO177", "Status": "Active",
                                            "PostCode": outcode, "Limit": 1000},
                                    headers={"Accept": "application/json"}, timeout=15)
                if resp.status_code != 200:
                    continue
                for o in resp.json().get("Organisations", []):
                    if o["OrgId"] not in seen:
                        seen.add(o["OrgId"])
                        organisations.append(o)
            except Exception:
                continue
        return organisations

    def _fetch_detail(self, org_id: str) -> dict:
        resp = requests.get(f"{BASE_URL}/{org_id}", timeout=10,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json().get("Organisation", {})
