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
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import httpx

from collector import normalize

log = logging.getLogger(__name__)

SOURCE_NAME = "openstreetmap"
SOURCE_URL = "https://www.openstreetmap.org/copyright"
TERMS_NOTE = "ODbL: credit \"© OpenStreetMap contributors\" wherever this data is shown."
# Public Overpass servers, tried in turn when one is busy (OVERPASS_URLS overrides).
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
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

AMENITIES = ("clinic", "doctors", "hospital", "social_facility", "school", "kindergarten")

# The server only does cheap tag lookups; matching names against NAME_PATTERN
# on the server timed out on the public instances, so names are matched here.
# `.a out ids` echoes the governorate boundary, to tell "no places" apart from
# "boundary not found".
QUERY = """[out:json][timeout:{timeout}];
area["ISO3166-2"="{iso}"]->.a;
(
  nwr(area.a)["healthcare"];
  nwr(area.a)["amenity"~"^({amenities})$"];
);
out center tags;
.a out ids;"""

_NAME_RE = re.compile(NAME_PATTERN, re.I)
_SPECIALITY_RE = re.compile(SPECIALITY_PATTERN, re.I)
_HEALTHCARE_RE = re.compile(HEALTHCARE_PATTERN)


def is_relevant(element: Dict[str, Any]) -> bool:
    """Autism-related: a matching speciality or kind of therapist, or a clinic,
    centre or school whose name suggests autism, speech or behaviour services."""
    tags = element.get("tags") or {}
    if _SPECIALITY_RE.search(tags.get("healthcare:speciality", "")) or _HEALTHCARE_RE.match(tags.get("healthcare", "")):
        return True
    if not (tags.get("healthcare") or tags.get("amenity") in AMENITIES):
        return False
    return any(_NAME_RE.search(tags.get(k, "")) for k in ("name", "name:ar", "name:en"))

# OSM healthcare:speciality values -> our specialty codes.
SPECIALITY_CODES = {
    "child_psychiatry": "child_psychiatry", "paediatric_psychiatry": "child_psychiatry",
    "speech_therapy": "speech_therapy", "speech_and_language_therapy": "speech_therapy",
    "occupational_therapy": "occupational_therapy", "psychology": "psychology",
    "behavioural_therapy": "behaviour_therapy", "paediatric_neurology": "pediatric_neurology",
    "neuropaediatrics": "pediatric_neurology", "developmental_paediatrics": "dev_pediatrics",
}

_ARABIC = re.compile(r"[؀-ۿ]")


class OverpassBusy(Exception):
    """Every Overpass server was busy or failed for one governorate."""


class OsmClient:
    def __init__(self, http: Optional[httpx.Client] = None, pause_seconds: float = 2.0, timeout: int = 180,
                 urls: Sequence[str] = OVERPASS_URLS, attempts: int = 4, backoff_seconds: float = 15):
        # IPv4 only: shared hosts often resolve IPv6 addresses they cannot reach.
        self.http = http or httpx.Client(timeout=timeout + 30, headers={"User-Agent": USER_AGENT},
                                         transport=httpx.HTTPTransport(local_address="0.0.0.0"))
        self.pause_seconds = pause_seconds
        self.timeout = timeout
        self.urls = list(urls) or list(OVERPASS_URLS)
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.current = 0            # index of the server that answered last
        self.requests_made = 0

    def _wait(self) -> None:
        if self.requests_made and self.pause_seconds:
            time.sleep(self.pause_seconds)
        self.requests_made += 1

    def search_governorate(self, code: str) -> List[Dict[str, Any]]:
        """All matching elements in one governorate. A busy, failed or timed-out
        server is retried on the next server after a growing pause; OverpassBusy
        once every attempt failed. A partial answer is never returned, so a
        place is only counted as missing when a full search left it out."""
        query = QUERY.format(timeout=self.timeout, iso=GOVERNORATE_ISO[code], amenities="|".join(AMENITIES))
        problem = ""
        for attempt in range(1, self.attempts + 1):
            url = self.urls[self.current]
            self._wait()
            try:
                resp = self.http.post(url, data={"data": query})
                if resp.status_code == 200:
                    body = resp.json()
                    remark = body.get("remark") or ""
                    elements = body.get("elements", [])
                    if "error" in remark.lower():
                        problem = f"incomplete answer: {remark.strip()[:120]}"
                    elif not any(e.get("type") == "area" for e in elements):
                        # Not worth retrying elsewhere: the boundary tag is missing from OSM.
                        raise OverpassBusy(f"boundary {GOVERNORATE_ISO[code]} not found in OpenStreetMap")
                    else:
                        return [e for e in elements if e.get("type") != "area" and is_relevant(e)]
                elif resp.status_code in (429,) or resp.status_code >= 500:
                    problem = f"HTTP {resp.status_code} {_snippet(resp.text)}"
                else:
                    resp.raise_for_status()
            except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
                problem = f"{type(exc).__name__}: {exc}"
            if attempt == self.attempts:
                break
            pause = min(self.backoff_seconds * 2 ** (attempt - 1), 120)
            self.current = (self.current + 1) % len(self.urls)
            log.warning("%s: %s from %s; trying %s in %ss", code, problem, _host(url),
                        _host(self.urls[self.current]), int(pause))
            if pause and self.pause_seconds:
                time.sleep(pause)
        raise OverpassBusy(f"{problem} (after {self.attempts} attempts)")

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


def _snippet(text: str) -> str:
    """The readable part of an error page, for the log."""
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())[:160]


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


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


def search(client: OsmClient, governorates: List[str],
           retry_pause: float = 300) -> Iterator[Tuple[str, Optional[Exception], List]]:
    """(governorate, error or None, elements) for each governorate. Governorates
    that fail are tried once more at the end, when the servers may be quieter."""
    failed = []
    for code in governorates:
        try:
            yield code, None, client.search_governorate(code)
        except Exception as exc:  # one busy or failed area must not stop the others
            log.warning("overpass failed for %s, trying again at the end: %s", code, exc)
            failed.append(code)
    if failed and retry_pause and getattr(client, "pause_seconds", 0):
        log.info("waiting %ss before retrying %s", int(retry_pause), ", ".join(failed))
        time.sleep(retry_pause)
    for code in failed:
        try:
            yield code, None, client.search_governorate(code)
        except Exception as exc:
            log.error("overpass failed for %s; it is retried on the next run: %s", code, exc)
            yield code, exc, []
