"""Abstract base class for all data sources."""
from abc import ABC, abstractmethod


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        """
        Fetch organisations near (lat, lon) within radius_km.
        Returns list of dicts matching the organisations table schema plus
        an optional 'contacts' key containing a list of contact dicts.
        """
        ...
