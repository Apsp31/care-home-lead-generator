"""Companies House Public Data API — solicitors, estate agents, wealth managers, IFAs.
Named directors/partners fetched via the officers endpoint.
"""
import os
import time
import requests
from .base import DataSource
from .geocoder import haversine_km, postcode_to_latlon

CH_BASE = "https://api.company-information.service.gov.uk"

# SIC codes → org_type
SIC_TYPES = {
    "69102": "solicitor",         # solicitors
    "68310": "estate_agent",      # estate agents
    "66300": "wealth_manager",    # fund/wealth management
    "64999": "financial_adviser", # financial service activities NEC (IFAs)
    "66220": "financial_adviser", # activities of insurance agents
    "66290": "financial_adviser", # other activities auxiliary to finance
}

# Role placeholders used when no named officer is available
ROLE_PLACEHOLDERS = {
    "solicitor": [
        {"name": "", "role": "Senior Partner — Private Client / Wills & LPA",
         "source_notes": "Target: private client, wills, probate or PoA department"},
        {"name": "", "role": "Head of Probate & Estate Planning",
         "source_notes": "Target: estate planning and elderly client work"},
    ],
    "estate_agent": [
        {"name": "", "role": "Branch Manager",
         "source_notes": "Target: later living, downsizing or probate property specialist"},
        {"name": "", "role": "Later Living / Downsizing Specialist",
         "source_notes": "Direct referral relationship for clients selling home to fund care"},
    ],
    "wealth_manager": [
        {"name": "", "role": "Client Relationship Manager",
         "source_notes": "Manages HNW client portfolios; advises on care cost planning"},
        {"name": "", "role": "Head of Private Wealth",
         "source_notes": "Senior contact for care fees planning at point of need"},
    ],
    "financial_adviser": [
        {"name": "", "role": "Independent Financial Adviser (Care Fees Planning)",
         "source_notes": "SOLLA-accredited IFAs introduce clients planning or needing residential care"},
    ],
}

# Officer roles to extract (filter out secretaries, nominees etc.)
RELEVANT_OFFICER_ROLES = {
    "director", "llp-member", "llp-designated-member",
    "managing-officer", "corporate-managing-officer",
}


def _format_name(ch_name: str) -> str:
    """Convert 'LASTNAME, Firstname Middle' → 'Firstname Middle Lastname'."""
    if "," in ch_name:
        parts = ch_name.split(",", 1)
        lastname = parts[0].strip().title()
        firstnames = parts[1].strip().title()
        return f"{firstnames} {lastname}"
    return ch_name.title()


class CompaniesHouseSource(DataSource):
    name = "companies_house"

    def __init__(self):
        self.api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        self._geocode_cache: dict[str, tuple[float, float]] = {}

    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        if not self.api_key:
            print("[companies_house] No API key — set COMPANIES_HOUSE_API_KEY in .env. Skipping.")
            return []

        results = []
        seen = set()

        for sic_code, org_type in SIC_TYPES.items():
            try:
                orgs = self._search_by_sic(sic_code, org_type, lat, lon, radius_km)
                for org in orgs:
                    key = org["source_id"]
                    if key not in seen:
                        seen.add(key)
                        results.append(org)
            except Exception as e:
                print(f"[companies_house] Error fetching SIC {sic_code}: {e}")

        return results

    def _search_by_sic(self, sic_code: str, org_type: str,
                       lat: float, lon: float, radius_km: float) -> list[dict]:
        resp = requests.get(
            f"{CH_BASE}/advanced-search/companies",
            auth=(self.api_key, ""),
            params={
                "sic_codes": sic_code,
                "company_status": "active",
                "size": 100,
                "start_index": 0,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[companies_house] HTTP {resp.status_code} for SIC {sic_code}")
            return []

        items = resp.json().get("items", [])
        results = []

        for item in items:
            addr = item.get("registered_office_address", {})
            postcode = addr.get("postal_code", "")
            if not postcode:
                continue

            try:
                org_lat, org_lon = self._geocode(postcode)
            except Exception:
                continue

            dist = haversine_km(lat, lon, org_lat, org_lon)
            if dist > radius_km:
                continue

            company_number = item.get("company_number", "")

            # Fetch named officers
            contacts = self._get_officers(company_number, org_type)

            results.append({
                "name": item.get("company_name", "").title(),
                "org_type": org_type,
                "source": self.name,
                "source_id": company_number,
                "address_line1": " ".join(filter(None, [addr.get("premises", ""), addr.get("address_line_1", "")])),
                "address_line2": addr.get("address_line_2", ""),
                "town": addr.get("locality", ""),
                "postcode": postcode,
                "lat": org_lat,
                "lon": org_lon,
                "distance_km": round(dist, 2),
                "phone": "",
                "website": "",
                "contacts": contacts,
            })

        return results

    def _geocode(self, postcode: str) -> tuple[float, float]:
        key = postcode.replace(" ", "").upper()
        if key not in self._geocode_cache:
            self._geocode_cache[key] = postcode_to_latlon(postcode)
        return self._geocode_cache[key]

    def _get_officers(self, company_number: str, org_type: str) -> list[dict]:
        """Fetch named active officers. Falls back to role placeholders on error."""
        if not company_number:
            return ROLE_PLACEHOLDERS.get(org_type, [])
        try:
            time.sleep(0.1)  # respect 600 req/5min rate limit
            resp = requests.get(
                f"{CH_BASE}/company/{company_number}/officers",
                auth=(self.api_key, ""),
                params={"items_per_page": 20},
                timeout=10,
            )
            if resp.status_code != 200:
                return ROLE_PLACEHOLDERS.get(org_type, [])

            officers = resp.json().get("items", [])
            contacts = []
            for officer in officers:
                # Skip resigned officers
                if officer.get("resigned_on"):
                    continue
                role = officer.get("officer_role", "")
                if role not in RELEVANT_OFFICER_ROLES:
                    continue
                name = _format_name(officer.get("name", ""))
                if not name:
                    continue
                contacts.append({
                    "name": name,
                    "role": _officer_role_label(role, org_type),
                    "source_notes": f"Companies House officer — {role}",
                })

            # Always include at least one role placeholder so the contact is actionable
            if not contacts:
                return ROLE_PLACEHOLDERS.get(org_type, [])

            # Supplement named officers with a role placeholder for unlisted contacts
            placeholders = ROLE_PLACEHOLDERS.get(org_type, [])
            if placeholders:
                contacts.append({**placeholders[0], "name": ""})

            return contacts[:4]  # cap at 4 contacts per org

        except Exception as e:
            print(f"[companies_house] Officers fetch failed for {company_number}: {e}")
            return ROLE_PLACEHOLDERS.get(org_type, [])


def _officer_role_label(role: str, org_type: str) -> str:
    role_map = {
        "director":                  "Director",
        "llp-member":                "LLP Member / Partner",
        "llp-designated-member":     "Designated LLP Member",
        "managing-officer":          "Managing Officer",
        "corporate-managing-officer": "Managing Officer",
    }
    base = role_map.get(role, role.replace("-", " ").title())
    # Add context hint per org type
    if org_type == "solicitor":
        return f"{base} (Solicitor)"
    if org_type == "wealth_manager":
        return f"{base} (Wealth Manager)"
    if org_type == "financial_adviser":
        return f"{base} (IFA)"
    if org_type == "estate_agent":
        return f"{base} (Estate Agent)"
    return base
