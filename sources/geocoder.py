"""Postcode geocoding via postcodes.io — no auth required."""
import re
import time as _time
import requests

_geocode_place_cache: dict[str, tuple[float, float] | None] = {}
_nominatim_last: float = 0.0


def geocode_place(name: str) -> tuple[float, float] | None:
    """Geocode a UK place name → (lat, lon) via Nominatim.
    Cached and rate-limited to 1 req/sec (Nominatim policy)."""
    global _nominatim_last
    key = name.strip().lower()
    if key in _geocode_place_cache:
        return _geocode_place_cache[key]
    wait = 1.0 - (_time.time() - _nominatim_last)
    if wait > 0:
        _time.sleep(wait)
    _nominatim_last = _time.time()
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": name, "countrycodes": "gb", "format": "json", "limit": 1},
            headers={"User-Agent": "CareHomeLeadGenerator/1.0"},
            timeout=8,
        )
        if resp.status_code == 200 and resp.json():
            r = resp.json()[0]
            result: tuple[float, float] = (float(r["lat"]), float(r["lon"]))
            _geocode_place_cache[key] = result
            return result
    except Exception:
        pass
    _geocode_place_cache[key] = None
    return None

_POSTCODE_RE = re.compile(r'^[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}$')


def postcode_to_latlon(postcode: str) -> tuple[float, float]:
    """Returns (lat, lon) for a UK postcode. Raises ValueError if not found."""
    postcode_clean = postcode.replace(" ", "").upper()
    if not _POSTCODE_RE.match(postcode_clean):
        raise ValueError(f"Invalid UK postcode: {postcode!r}")
    resp = requests.get(f"https://api.postcodes.io/postcodes/{postcode_clean}", timeout=10)
    if resp.status_code != 200:
        raise ValueError(f"Postcode not found: {postcode}")
    data = resp.json()["result"]
    return data["latitude"], data["longitude"]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
