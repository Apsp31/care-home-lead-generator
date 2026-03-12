"""Postcode geocoding via postcodes.io — no auth required."""
import requests


def postcode_to_latlon(postcode: str) -> tuple[float, float]:
    """Returns (lat, lon) for a UK postcode. Raises ValueError if not found."""
    postcode_clean = postcode.replace(" ", "").upper()
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
