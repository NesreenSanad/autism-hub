"""Text, phone and place normalisation shared by every source.

Every source turns its raw data into a "record": a flat dict with the keys in
RECORD_FIELDS. Values are normalised here so that comparing two runs, or two
sources, compares like with like.
"""
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

# Fields a record may carry. Anything else a source returns is dropped.
RECORD_FIELDS = (
    "name_ar", "name_en", "facility_type", "governorate_code", "city",
    "address_ar", "address_en", "lat", "lng",
    "phone", "whatsapp", "website", "facebook", "google_maps_url", "business_status",
    "provider_name_ar", "provider_name_en", "provider_type", "gender",
    "specialties", "services", "notes_ar",
)

FACILITY_TYPES = ("clinic", "center", "hospital", "school")
PROVIDER_TYPES = ("doctor", "therapist", "psychologist", "center", "school", "hospital_unit")

_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # diacritics + tatweel
_TITLES = re.compile(  # applied after letter unification, so أ.د is ا.د and دكتورة is دكتوره
    r"^(?:(?:ا\.?د|د|دكتور|دكتوره|الدكتور|الدكتوره|استاذ|الاستاذ|dr|prof|professor)"
    r"(?:[./]\s*|\s+))+",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def clean_text(value: Any) -> Optional[str]:
    """NFKC (fixes Arabic presentation forms copied from PDFs, e.g. ﻧﻬﺎل -> نهال),
    collapse whitespace, and turn empty strings into None."""
    if value is None:
        return None
    text = _SPACES.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()
    return text or None


def search_name(value: Optional[str]) -> str:
    """Name used for search and dedupe: lower case, no tashkeel, unified Arabic
    letters (أإآ->ا, ة->ه, ى->ي), and no titles such as "د." or "دكتور"."""
    text = clean_text(value) or ""
    text = _TASHKEEL.sub("", text).lower()
    text = (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                .replace("ة", "ه").replace("ى", "ي"))
    text = _TITLES.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return _SPACES.sub(" ", text).strip()


def phone(value: Any) -> Optional[str]:
    """Egyptian phone numbers in +20 format. Short hotlines (e.g. 19xxx) are
    kept as digits. Returns None for anything that does not look like a number."""
    text = clean_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text.translate(_ARABIC_DIGITS))
    if digits.startswith("0020"):
        digits = digits[4:]
    elif digits.startswith("20") and len(digits) >= 11:
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if 4 <= len(digits) <= 5:          # hotline such as 19xxx or 16xxx
        return digits
    if len(digits) == 10 and digits[0] == "1":   # mobile: 1x xxxx xxxx
        return "+20" + digits
    if 8 <= len(digits) <= 9 and digits[0] in "2345689":  # landline with area code
        return "+20" + digits
    return None


def is_mobile(number: Optional[str]) -> bool:
    return bool(number and number.startswith("+201"))


def url(value: Any) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text
    return text


def coord(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return round(float(str(value).translate(_ARABIC_DIGITS)), 6)
    except ValueError:
        return None


def code_list(value: Any, allowed: Iterable[str]) -> List[str]:
    """'speech; ot , aba' -> ['aba', 'ot', 'speech'], keeping only known codes."""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[;,،|]", str(value))
    allowed = set(allowed)
    return sorted({c.lower() for c in (clean_text(i) for i in items) if c and c.lower() in allowed})


class GovernorateMatcher:
    """Matches Arabic or English governorate names (or codes) to codes."""

    def __init__(self, rows: Iterable[tuple]):
        self._lookup: Dict[str, str] = {}
        for code, name_ar, name_en in rows:
            for key in (code, name_ar, name_en):
                self._lookup[self._key(key)] = code

    @staticmethod
    def _key(value: Optional[str]) -> str:
        text = search_name(value).replace("_", " ")
        text = re.sub(r"^(محافظه|governorate)\s+|\s+(governorate|محافظه)$", "", text)
        return text[2:] if text.startswith("ال") else text

    def match(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._lookup.get(self._key(value))

    def find_in(self, text: Optional[str]) -> Optional[str]:
        """Find a governorate mentioned in a free-text address (last match wins,
        since addresses end with the governorate)."""
        if not text:
            return None
        found = None
        for part in re.split(r"[,،]", text):
            found = self.match(part) or found
        return found


def facility_type(name: str, types: Iterable[str]) -> str:
    """Guess the facility type from its name and the source's place types
    (Google types, or OSM amenity/healthcare values)."""
    types = set(types)
    if "hospital" in types:
        return "hospital"
    if {"school", "primary_school", "secondary_school"} & types:
        return "school"
    if re.search(r"مركز|center|centre|جمعي|مؤسس", name, re.IGNORECASE):
        return "center"
    if {"doctor", "doctors", "clinic"} & types or re.search(r"عياد|clinic|دكتور|د\.", name, re.IGNORECASE):
        return "clinic"
    return "center"


def build_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep known fields, clean text, and drop empty values."""
    record: Dict[str, Any] = {}
    for key in RECORD_FIELDS:
        value = raw.get(key)
        if isinstance(value, str):
            value = clean_text(value)
        if value in (None, "", []):
            continue
        record[key] = value
    return record


def content_hash(record: Dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
