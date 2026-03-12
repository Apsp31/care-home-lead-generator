"""NHS ODS ORD API — GP practices, NHS trusts, PCNs."""
import requests
from .base import DataSource
from .geocoder import haversine_km

# ODS role codes
ROLE_CODES = {
    "RO177": "GP",           # GP practices
    "RO197": "hospital",     # NHS trusts / acute
    "RO213": "PCN",          # Primary Care Networks
}

BASE_URL = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"


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
        params = {
            "PrimaryRoleId": role_code,
            "Status": "Active",
            "Limit": 1000,
            "Offset": 0,
        }
        resp = requests.get(BASE_URL, params=params, timeout=15,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        organisations = data.get("Organisations", [])

        results = []
        for org in organisations:
            # Fetch detail to get address + location
            try:
                detail = self._fetch_detail(org["OrgId"])
            except Exception:
                continue

            geo = detail.get("GeoLoc", {}).get("Location", {})
            org_lat = geo.get("lat")
            org_lon = geo.get("lng")

            if org_lat is None or org_lon is None:
                continue

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km:
                continue

            addr = detail.get("Rels", {})
            addr_parts = detail.get("Addresses", [{}])[0] if detail.get("Addresses") else {}

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

            raw_contacts = detail.get("Contacts", [])
            phone   = next((c["value"] for c in raw_contacts if c.get("type") == "tel"), "")
            website = next((c["value"] for c in raw_contacts if c.get("type") == "http"), "")
            email   = next((c["value"] for c in raw_contacts if c.get("type") == "email"), "")

            results.append({
                "name": detail.get("Name", org.get("Name", "")),
                "org_type": org_type,
                "source": self.name,
                "source_id": org["OrgId"],
                "address_line1": addr_parts.get("AddrLn1", ""),
                "address_line2": addr_parts.get("AddrLn2", ""),
                "town": addr_parts.get("Town", ""),
                "postcode": addr_parts.get("PostCode", ""),
                "lat": org_lat,
                "lon": org_lon,
                "distance_km": round(dist, 2),
                "phone": phone,
                "email": email,
                "website": website,
                "contacts": contacts,
            })

        return results

    def _fetch_detail(self, org_id: str) -> dict:
        resp = requests.get(f"{BASE_URL}/{org_id}", timeout=10,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json().get("Organisation", {})
