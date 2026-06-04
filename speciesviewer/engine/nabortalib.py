import requests
import logging

logger = logging.getLogger("speciesviewer")

API_BASE = "https://api.biodiversitydata.nl/v2"


class NBASpecies:
    """Client for the Netherlands Biodiversity API (NBA).

    Provides vernacular (Dutch/English) name lookups via the Dutch Species
    Register (NSR) and Catalogue of Life (COL) datasets hosted at
    api.biodiversitydata.nl.  No authentication required.
    """

    TIMEOUT = 10  # seconds

    def __init__(self):
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------

    def get_vernacular_names(self, scientific_name):
        """Return (english, dutch) vernacular names for *scientific_name*.

        The scientific name should be the canonical binomial (e.g.
        "parus major").  Returns a tuple of lowercase strings; either
        value may be "" if no vernacular name was found.
        """
        results = self._query_taxon(scientific_name)
        if not results:
            return ("", "")

        english = ""
        dutch = ""
        for item in results:
            for vn in item.get("vernacularNames", []):
                lang = (vn.get("language") or "").lower()
                preferred = vn.get("preferred", False)
                name = (vn.get("name") or "").strip()
                if not name:
                    continue
                if lang in ("dutch", "nl") and (not dutch or preferred):
                    dutch = name.lower()
                elif lang in ("english", "en") and (not english or preferred):
                    english = name.lower()
                if english and dutch:
                    break

        return (english, dutch)

    def get_all_vernacular_names(self, scientific_name):
        """Return all vernacular names grouped by language.

        Returns a dict like {"dutch": ["koolmees"], "english": ["great tit"]}.
        """
        results = self._query_taxon(scientific_name)
        if not results:
            return {}

        by_lang = {}
        for item in results:
            for vn in item.get("vernacularNames", []):
                lang = (vn.get("language") or "").lower()
                if lang in ("en",):
                    lang = "english"
                elif lang in ("nl",):
                    lang = "dutch"
                name = (vn.get("name") or "").strip()
                if name:
                    by_lang.setdefault(lang, []).append(name.lower())
        return by_lang

    def taxon_exists(self, scientific_name):
        """Return True if *scientific_name* is known in the NBA."""
        results = self._query_taxon(scientific_name)
        return len(results) > 0

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _query_taxon(self, scientific_name):
        """Query the NBA taxon endpoint and return the resultSet list."""
        name_group = scientific_name.strip().lower()
        url = (
            f"{API_BASE}/taxon/query/"
            f"?acceptedName.scientificNameGroup={name_group}"
            f"&_fields=vernacularNames,acceptedName.fullScientificName,sourceSystem.code"
        )
        try:
            resp = self.session.get(url, timeout=self.TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return [item["item"] for item in data.get("resultSet", []) if "item" in item]
        except requests.RequestException as exc:
            logger.warning(f"NBA API request failed for '{scientific_name}': {exc}")
            return []
