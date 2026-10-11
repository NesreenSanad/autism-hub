"""Manual CSV lists (NGOs, hospitals, lists you checked yourself).

Column names are in backend/collector/providers_template.csv. Only `name_ar`
(or `provider_name_ar`) is required. Lists of codes (specialties, services)
are separated by ";". Save from Excel as "CSV UTF-8".
"""
import csv
import hashlib
import logging
from typing import Any, Dict, Iterator, List, Tuple

from collector import normalize

log = logging.getLogger(__name__)

COLUMNS = (
    "id", "name_ar", "name_en", "facility_type", "governorate", "city", "address_ar", "address_en",
    "lat", "lng", "phone", "whatsapp", "website", "facebook",
    "provider_name_ar", "provider_name_en", "provider_type", "gender",
    "specialties", "services", "notes_ar", "source_url",
)


def read_rows(path: str) -> Iterator[Tuple[int, Dict[str, str]]]:
    """(line number, row) for each row; utf-8-sig drops Excel's byte order mark."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        unknown = set(reader.fieldnames or []) - set(COLUMNS)
        if unknown:
            log.warning("ignoring unknown columns: %s", ", ".join(sorted(unknown)))
        for row in reader:
            yield reader.line_num, {k.strip(): (v or "").strip() for k, v in row.items() if k}


def to_record(row: Dict[str, str], governorates: normalize.GovernorateMatcher,
              specialty_codes: List[str], service_codes: List[str]) -> Tuple[str, Dict[str, Any], List[str]]:
    """CSV row -> (external id, record, problems). A row with problems that
    make it unusable comes back with an empty record."""
    problems: List[str] = []
    name_ar = normalize.clean_text(row.get("name_ar"))
    provider_name_ar = normalize.clean_text(row.get("provider_name_ar"))
    if not (name_ar or provider_name_ar):
        return "", {}, ["needs name_ar or provider_name_ar"]

    governorate = governorates.match(row.get("governorate"))
    if row.get("governorate") and not governorate:
        problems.append(f"unknown governorate {row['governorate']!r}")

    provider_type = (row.get("provider_type") or "").lower() or None
    if provider_type and provider_type not in normalize.PROVIDER_TYPES:
        problems.append(f"unknown provider_type {provider_type!r}")
        provider_type = None
    facility_type = (row.get("facility_type") or "").lower() or None
    if facility_type and facility_type not in normalize.FACILITY_TYPES:
        problems.append(f"unknown facility_type {facility_type!r}")
        facility_type = None
    gender = (row.get("gender") or "").lower() or None
    if gender and gender not in ("female", "male"):
        problems.append(f"gender must be female or male, not {gender!r}")
        gender = None

    phone = normalize.phone(row.get("phone"))
    if row.get("phone") and not phone:
        problems.append(f"could not read phone {row['phone']!r}")

    for column, codes in (("specialties", specialty_codes), ("services", service_codes)):
        given = {c.strip().lower() for c in (row.get(column) or "").replace(",", ";").split(";") if c.strip()}
        if given - set(codes):
            problems.append(f"unknown {column}: {', '.join(sorted(given - set(codes)))}")

    record = normalize.build_record({
        "name_ar": name_ar,
        "name_en": row.get("name_en"),
        "facility_type": facility_type or ("clinic" if provider_name_ar else "center"),
        "governorate_code": governorate,
        "city": row.get("city"),
        "address_ar": row.get("address_ar"),
        "address_en": row.get("address_en"),
        "lat": normalize.coord(row.get("lat")),
        "lng": normalize.coord(row.get("lng")),
        "phone": phone,
        "whatsapp": normalize.phone(row.get("whatsapp")),
        "website": normalize.url(row.get("website")),
        "facebook": normalize.url(row.get("facebook")),
        "provider_name_ar": provider_name_ar,
        "provider_name_en": row.get("provider_name_en"),
        "provider_type": provider_type or ("doctor" if provider_name_ar else None),
        "gender": gender,
        "specialties": normalize.code_list(row.get("specialties"), specialty_codes),
        "services": normalize.code_list(row.get("services"), service_codes),
        "notes_ar": row.get("notes_ar"),
    })

    external_id = row.get("id") or hashlib.sha1(
        f"{normalize.search_name(provider_name_ar or '')}|{normalize.search_name(name_ar or '')}|{phone or ''}"
        .encode("utf-8")).hexdigest()[:16]
    return external_id, record, problems
