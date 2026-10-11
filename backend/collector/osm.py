"""OpenStreetMap source: free, no API key.

Overpass API finds clinics, centres and therapists related to autism in each
governorate; Nominatim turns a CSV row's address into coordinates.

OSM data is under the Open Database Licence (ODbL): it may be stored and
shown, but the app must credit "© OpenStreetMap contributors" wherever it
shows it. Both services are run by volunteers, so the client identifies
itself, waits between requests and asks for little.
"""
import logging
import re
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from collector import normalize

log = logging.getLogger(__name__)

SOURCE_NAME = "openstreetmap"
SOURCE_URL = "https://www.openstreetmap.org/copyright"
TERMS_NOTE = "ODbL: credit \"© OpenStreetMap contributors\" wherever this data is shown."
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "autism-hub-collector/0.1 (+https://github.com/NesreenSanad/autism-hub)"

# ISO 3166-2 codes of Egypt's governorates, as tagged on their OSM boundaries.
GOVERNORATE_ISO = {
    "cairo": "EG-C", "giza": "EG-GZ", "alexandria": "EG-ALX", "qalyubia": "EG-KB",
    "sharqia": "EG-SHR", "dakahlia": "EG-DK", "gharbia": "EG-GH", "monufia": "EG-MNF",
    "beheira": "EG-BH", "kafr_el_sheikh": "EG-KFS", "damietta": "EG-DT", "port_said": "EG-PTS",
    "ismailia": "EG-IS", "suez": "EG-SUZ", "north_sinai": "EG-SIN", "south_sinai": "EG-JS",
    "faiyum": "EG-FYM", "beni_suef": "EG-BNS", "minya": "EG-MN", "asyut": "EG-AST",
    "sohag": "EG-SHG", "qena": "EG-KN", "luxor": "EG-LX", "aswan": "EG-ASN",
    "red_sea": "EG-BA", "new_valley": "EG-WAD", "matrouh": "EG-MT",
}

# Names that suggest autism, development, speech or behaviour services.
NAME_PATTERN = (
    "توحد|اوتيزم|أوتيزم|autis|تخاطب|تعديل سلوك|تنمية مهارات|احتياجات خاصة|ذوي الهمم|"
    "صعوبات تعلم|تأهيل|نفسي|special needs|speech|psychiat|child development|rehabilitation"
)
SPECIALITY_PATTERN = "psychiatr|speech|occupational|psycholog|behavio|autis|development"
HEALTHCARE_PATTERN = "^(speech_therapist|occupational_therapist|psychotherapist|psychologist|counselling|rehabilitation)$"

QUERY = """[out:json][timeout:{timeout}];
area["ISO3166-2"="{iso}"]->.a;
(
  nwr(area.a)["healthcare:speciality"~"{speciality}",i];
  nwr(area.a)["healthcare"~"{healthcare}"];
  nwr(area.a)["name"~"{names}",i]["amenity"~"^(clinic|doctors|hospital|social_facility|school|kindergarten)$"];
  nwr(area.a)["name"~"{names}",i]["healthcare"];
  nwr(area.a)["name:en"~"{names}",i]["healthcare"];
);
out center tags;"""

# OSM healthcare:speciality values -> our specialty codes.
SPECIALITY_CODES = {
    "child_psychiatry": "child_psychiatry", "paediatric_psychiatry": "child_psychiatry",
    "speech_therapy": "speech_therapy", "speech_and_language_therapy": "speech_therapy",
    "occupational_therapy": "occupational_therapy", "psychology": "psychology",
    "behavioural_therapy": "behaviour_therapy", "paediatric_neurology": "pediatric_neurology",
    "neuropaediatrics": "pediatric_neurology", "developmental_paediatrics": "dev_pediatrics",
}

_ARABIC = re.compile(r"[؀-ۿ]")


class OsmClient:
    def __init__(self, http: Optional[httpx.Client] = None, pause_seconds: float = 2.0, timeout: int = 120):
        self.http = http or httpx.Client(timeout=timeout + 30, headers={"User-Agent": USER_AGENT})
        self.pause_seconds = pause_seconds
        self.timeout = timeout
        self.requests_made = 0

    def _wait(self) -> None:
        if self.requests_made and self.pause_seconds:
            time.sleep(self.pause_seconds)
        self.requests_made += 1

    def search_governorate(self, code: str) -> List[Dict[str, Any]]:
        query = QUERY.format(timeout=self.timeout, iso=GOVERNORATE_ISO[code], speciality=SPECIALITY_PATTERN,
                             healthcare=HEALTHCARE_PATTERN, names=NAME_PATTERN)
        for attempt in (1, 2):
            self._wait()
            resp = self.http.post(OVERPASS_URL, data={"data": query})
            if resp.status_code in (429, 504) and attempt == 1:
                log.warning("overpass busy (%s), retrying in 30s", resp.status_code)
                time.sleep(30 if self.pause_seconds else 0)
                continue
            resp.raise_for_status()
            return resp.json().get("elements", [])
        return []

    def geocode(self, query: str) -> Optional[Tuple[float, float]]:
        """Address -> (lat, lng) with Nominatim (its limit is one request per second)."""
        self._wait()
        resp = self.http.get(NOMINATIM_URL, params={"q": query, "format": "jsonv2", "countrycodes": "eg",
                                                    "limit": 1, "accept-language": "ar"})
        resp.raise_for_status()
        hits = resp.json()
        if not hits:
            return None
        return normalize.coord(hits[0]["lat"]), normalize.coord(hits[0]["lon"])


def _first(tags: Dict[str, str], *keys: str) -> Optional[str]:
    for key in keys:
        if tags.get(key):
            return tags[key].split(";")[0].strip()
    return None


def _address(tags: Dict[str, str]) -> Optional[str]:
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    parts = [street, tags.get("addr:suburb") or tags.get("addr:district"), tags.get("addr:city")]
    return "، ".join(p for p in parts if p) or None


def to_record(element: Dict[str, Any], governorate_code: Optional[str]) -> Tuple[str, Dict[str, Any], str]:
    """Overpass element -> (external id, normalised record, OSM page URL)."""
    tags = element.get("tags") or {}
    name = normalize.clean_text(tags.get("name")) or ""
    name_ar = tags.get("name:ar") or (name if _ARABIC.search(name) else None)
    name_en = tags.get("name:en") or (name if name and not _ARABIC.search(name) else None)
    address = _address(tags)
    point = element if "lat" in element else (element.get("center") or {})
    specialities = re.split(r"[;,]", tags.get("healthcare:speciality", ""))

    record = normalize.build_record({
        "name_ar": name_ar,
        "name_en": name_en,
        "facility_type": normalize.facility_type(name, [tags.get("amenity", ""), tags.get("healthcare", "")]),
        "governorate_code": governorate_code,
        "city": tags.get("addr:city"),
        "address_ar" if address and _ARABIC.search(address) else "address_en": address,
        "lat": normalize.coord(point.get("lat")),
        "lng": normalize.coord(point.get("lon")),
        "phone": normalize.phone(_first(tags, "phone", "contact:phone", "contact:mobile")),
        "whatsapp": normalize.phone(_first(tags, "contact:whatsapp")),
        "website": normalize.url(_first(tags, "website", "contact:website")),
        "facebook": normalize.url(_first(tags, "contact:facebook", "facebook")),
        "specialties": sorted({SPECIALITY_CODES[s.strip()] for s in specialities if s.strip() in SPECIALITY_CODES}),
    })
    external_id = f"{element['type']}/{element['id']}"
    return external_id, record, f"https://www.openstreetmap.org/{external_id}"


def search(client: OsmClient, governorates: List[str]) -> Iterator[Tuple[str, Optional[Exception], List]]:
    """(governorate, error or None, elements) for each governorate."""
    for code in governorates:
        try:
            yield code, None, client.search_governorate(code)
        except Exception as exc:  # one busy or failed area must not stop the others
            log.error("overpass failed for %s: %s", code, exc)
            yield code, exc, []
