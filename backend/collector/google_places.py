"""Google Places API (New) source.

Discovery: Text Search for autism-related queries in each configured
governorate. Refresh: Place Details for places we already know.

Google's terms let us keep `place_id` indefinitely; everything else must be
refreshed, which is why the daily job re-checks known places on a schedule.
Every request is billed, so the client stops at a per-run request budget.
"""
import logging
import re
from typing import Any, Dict, Iterator, Optional, Tuple

import httpx

from collector import normalize

log = logging.getLogger(__name__)

BASE_URL = "https://places.googleapis.com/v1"
SOURCE_NAME = "google_places"

PLACE_FIELDS = (
    "id,displayName,formattedAddress,addressComponents,location,"
    "nationalPhoneNumber,internationalPhoneNumber,websiteUri,googleMapsUri,"
    "businessStatus,types"
)

# Each query is sent once per governorate as "<query> في <governorate>".
DEFAULT_QUERIES = (
    "مركز توحد",
    "دكتور نفسي أطفال",
    "مركز تخاطب وتعديل سلوك",
    "دكتور مخ وأعصاب أطفال",
)

_ARABIC = re.compile(r"[؀-ۿ]")


class BudgetExceeded(Exception):
    pass


class PlacesClient:
    def __init__(self, api_key: str, max_requests: int, http: Optional[httpx.Client] = None):
        self.api_key = api_key
        self.max_requests = max_requests
        self.requests_made = 0
        self.http = http or httpx.Client(timeout=20)

    def _request(self, method: str, path: str, field_mask: str, **kwargs) -> httpx.Response:
        if self.requests_made >= self.max_requests:
            raise BudgetExceeded(f"stopped at the budget of {self.max_requests} requests")
        self.requests_made += 1
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": field_mask}
        for attempt in (1, 2):
            resp = self.http.request(method, BASE_URL + path, headers=headers, **kwargs)
            if resp.status_code not in (429, 500, 502, 503) or attempt == 2:
                return resp
            log.warning("google places %s returned %s, retrying once", path, resp.status_code)
        return resp

    def search(self, query: str, max_pages: int) -> Iterator[Dict[str, Any]]:
        mask = ",".join("places." + f for f in PLACE_FIELDS.split(",")) + ",nextPageToken"
        body: Dict[str, Any] = {"textQuery": query, "languageCode": "ar", "regionCode": "EG",
                                "pageSize": 20}
        for _ in range(max_pages):
            resp = self._request("POST", "/places:searchText", mask, json=body)
            resp.raise_for_status()
            data = resp.json()
            yield from data.get("places", [])
            token = data.get("nextPageToken")
            if not token:
                return
            body["pageToken"] = token

    def details(self, place_id: str) -> Optional[Dict[str, Any]]:
        """The place, or None if Google no longer knows it."""
        resp = self._request("GET", f"/places/{place_id}", PLACE_FIELDS,
                             params={"languageCode": "ar", "regionCode": "EG"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def to_record(place: Dict[str, Any], governorates: normalize.GovernorateMatcher,
              fallback_governorate: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Google place -> (place_id, normalised record)."""
    name = normalize.clean_text((place.get("displayName") or {}).get("text")) or ""
    address = place.get("formattedAddress")
    types = place.get("types") or []

    governorate = None
    for comp in place.get("addressComponents") or []:
        if "administrative_area_level_1" in comp.get("types", []):
            governorate = governorates.match(comp.get("longText")) or governorates.match(comp.get("shortText"))
    governorate = governorate or governorates.find_in(address) or fallback_governorate

    location = place.get("location") or {}
    raw = {
        "name_ar" if _ARABIC.search(name) else "name_en": name,
        "facility_type": normalize.facility_type(name, types),
        "governorate_code": governorate,
        "address_ar": address,
        "lat": normalize.coord(location.get("latitude")),
        "lng": normalize.coord(location.get("longitude")),
        "phone": normalize.phone(place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber")),
        "website": normalize.url(place.get("websiteUri")),
        "google_maps_url": place.get("googleMapsUri"),
        "business_status": place.get("businessStatus"),
    }
    return place["id"], normalize.build_record(raw)
