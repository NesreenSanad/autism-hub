"""Saving records, detecting changes, and the review queue.

How a record moves through the database:

1. Each record a source gives us is kept in `source_records` (one row per
   Google place or CSV row) with its latest normalised data and a hash.
2. A new record is either approved straight away (your own CSV lists) or
   waits with review_status = 'pending' (Google results). Approving a record
   links it to an existing facility (same phone, or same name close by) or
   creates the facility, provider, contacts and specialties.
3. When a known record changes, every changed field gets an `observations`
   row and a `change_events` row. Low-risk fields (website, English name,
   services, ...) are applied at once. Risky ones (phone, address, name,
   location, closed) wait as 'pending'; set them to 'approved' in Supabase
   and the next run applies them, or 'rejected' to ignore them.
4. Records a source stops returning count as missed; after several misses in
   a row the facility is marked 'possibly_stale', and seen again it is
   'active' again.
"""
import difflib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from collector import normalize

log = logging.getLogger(__name__)

# Fields whose changes are applied without review. Everything else is risky.
LOW_RISK_FIELDS = {
    "name_en", "provider_name_en", "website", "facebook", "google_maps_url",
    "city", "notes_ar", "specialties", "services",
}
FACILITY_COLUMNS = ("name_ar", "name_en", "facility_type", "governorate_code", "address_ar", "address_en")
CONTACT_KINDS = {"phone": ("phone", "mobile"), "whatsapp": ("whatsapp",),
                 "website": ("website",), "facebook": ("facebook",)}
# Listings a person asked us to remove, or that we closed, are never revived.
LIVE_STATUSES = ("active", "possibly_stale")
DUPLICATE_DISTANCE_M = 300
DUPLICATE_NAME_RATIO = 0.6


def connect(database_url: str) -> psycopg.Connection:
    # prepare_threshold=None: the Supabase pooler (port 6543) does not support
    # prepared statements. Autocommit, with one transaction per record.
    return psycopg.connect(database_url, autocommit=True, prepare_threshold=None, row_factory=dict_row)


def now() -> datetime:
    return datetime.now(timezone.utc)


# Sources, runs, lookups ---------------------------------------------------
def source_id(conn, name: str, url: Optional[str] = None, terms_note: Optional[str] = None) -> int:
    conn.execute("insert into sources (name, url, terms_note) values (%s, %s, %s) "
                 "on conflict (name) do nothing", (name, url, terms_note))
    return conn.execute("select id from sources where name = %s", (name,)).fetchone()["id"]


def governorate_matcher(conn) -> normalize.GovernorateMatcher:
    rows = conn.execute("select code, name_ar, name_en from governorates").fetchall()
    return normalize.GovernorateMatcher((r["code"], r["name_ar"], r["name_en"]) for r in rows)


def start_run(conn, job: str, source: Optional[int] = None) -> int:
    return conn.execute("insert into ingest_runs (job, source_id) values (%s, %s) returning id",
                        (job, source)).fetchone()["id"]


def finish_run(conn, run_id: int, stats: Dict[str, Any], error: Optional[str] = None) -> None:
    conn.execute("update ingest_runs set finished_at = now(), status = %s, stats = %s, error = %s "
                 "where id = %s", ("failed" if error else "ok", Jsonb(stats), error, run_id))


def last_ok_run(conn, job: str) -> Optional[datetime]:
    row = conn.execute("select max(started_at) as at from ingest_runs where job = %s and status = 'ok'",
                       (job,)).fetchone()
    return row["at"]


# Saving one record --------------------------------------------------------
def save_record(conn, source: int, external_id: str, record: Dict[str, Any],
                source_url: Optional[str] = None, auto_approve: bool = False) -> str:
    """Save one record from a source. Returns 'new', 'changed' or 'unchanged'."""
    record = normalize.build_record(record)
    digest = normalize.content_hash(record)
    with conn.transaction():
        row = conn.execute(
            "select * from source_records where source_id = %s and external_id = %s for update",
            (source, external_id)).fetchone()

        if row is None:
            row = conn.execute(
                "insert into source_records (source_id, external_id, source_url, data, content_hash, review_status) "
                "values (%s, %s, %s, %s, %s, %s) returning *",
                (source, external_id, source_url, Jsonb(record), digest,
                 "approved" if auto_approve else "pending")).fetchone()
            for field, value in record.items():
                _observe(conn, row, field, value)
            if row["review_status"] == "approved":
                link_record(conn, row)
            return "new"

        conn.execute("update source_records set last_seen_at = now(), last_checked_at = now(), "
                     "missed_runs = 0, source_url = coalesce(%s, source_url) where id = %s",
                     (source_url, row["id"]))
        if row["facility_id"]:
            _set_facility_status(conn, row, "active", only_from=("possibly_stale",))
        if row["provider_id"]:
            conn.execute("update providers set last_seen_at = now() where id = %s", (row["provider_id"],))

        if row["content_hash"] == digest:
            return "unchanged"

        old = row["data"]
        conn.execute("update source_records set data = %s, content_hash = %s where id = %s",
                     (Jsonb(record), digest, row["id"]))
        row["data"] = record
        for field in sorted(set(old) | set(record)):
            if old.get(field) == record.get(field):
                continue
            new_value = record.get(field)
            _observe(conn, row, field, new_value)
            if row["review_status"] != "approved":
                continue  # pending: the reviewer sees the latest data; rejected: ignored
            if not row["facility_id"]:
                link_record(conn, row)  # its facility was deleted; link again
                continue
            risky = field not in LOW_RISK_FIELDS or new_value is None
            event_id = conn.execute(
                "insert into change_events (provider_id, facility_id, field, old_value, new_value, "
                "review_status, risk, source_id, source_record_id) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (row["provider_id"], row["facility_id"], field, Jsonb(old.get(field)), Jsonb(new_value),
                 "pending" if risky else "applied", "high" if risky else "low",
                 row["source_id"], row["id"])).fetchone()["id"]
            if not risky:
                apply_change(conn, row, field, new_value)
            log.info("change %s on record %s: %s (%s)", event_id, row["id"], field,
                     "queued for review" if risky else "applied")
        return "changed"


def mark_missed(conn, record_id: int) -> None:
    conn.execute("update source_records set missed_runs = missed_runs + 1, last_checked_at = now() "
                 "where id = %s", (record_id,))


def mark_missing_since(conn, source: int, since: datetime) -> int:
    """For sources that send their whole list each time (CSV): every record not
    seen since `since` was missing from this import."""
    cur = conn.execute("update source_records set missed_runs = missed_runs + 1 "
                       "where source_id = %s and last_seen_at < %s and review_status <> 'rejected'",
                       (source, since))
    return cur.rowcount


def mark_stale(conn, after_misses: int) -> int:
    """Facilities that every linked source has missed `after_misses` times in a row."""
    with conn.transaction():
        rows = conn.execute(
            "update facilities f set status = 'possibly_stale' where status = 'active' "
            "and exists (select 1 from source_records r where r.facility_id = f.id) "
            "and not exists (select 1 from source_records r where r.facility_id = f.id "
            "  and r.review_status <> 'rejected' and r.missed_runs < %s) returning id",
            (after_misses,)).fetchall()
        for r in rows:
            conn.execute("insert into change_events (facility_id, field, old_value, new_value, review_status, risk) "
                         "values (%s, 'status', '\"active\"', '\"possibly_stale\"', 'applied', 'low')", (r["id"],))
    return len(rows)


# Review queue -------------------------------------------------------------
def apply_reviews(conn) -> Dict[str, int]:
    """Apply what a reviewer approved in Supabase since the last run."""
    stats = {"records_linked": 0, "changes_applied": 0}
    for row in conn.execute("select * from source_records where review_status = 'approved' "
                            "and facility_id is null").fetchall():
        with conn.transaction():
            link_record(conn, row)
        stats["records_linked"] += 1

    events = conn.execute("select id, source_record_id, field, new_value from change_events "
                          "where review_status = 'approved' order by id").fetchall()
    for event in events:
        with conn.transaction():
            row = conn.execute("select * from source_records where id = %s",
                               (event["source_record_id"],)).fetchone()
            if row and row["facility_id"]:
                apply_change(conn, row, event["field"], event["new_value"])
            conn.execute("update change_events set review_status = 'applied' where id = %s", (event["id"],))
        stats["changes_applied"] += 1
    return stats


# Linking a record to facilities and providers -----------------------------
def link_record(conn, row: Dict[str, Any]) -> None:
    """Attach an approved record to a matching facility, or create one, then
    fill in its provider, contacts, specialties and services."""
    data = row["data"]
    facility_id = _find_facility(conn, data) or _create_facility(conn, data)
    provider_id = row["provider_id"] or _find_or_create_provider(conn, data, facility_id)

    conn.execute("update source_records set facility_id = %s, provider_id = %s where id = %s",
                 (facility_id, provider_id, row["id"]))
    conn.execute("update observations set facility_id = %s, provider_id = %s "
                 "where source_record_id = %s and facility_id is null", (facility_id, provider_id, row["id"]))
    row["facility_id"], row["provider_id"] = facility_id, provider_id

    for field in CONTACT_KINDS:
        if data.get(field):
            _replace_contacts(conn, row, field, data[field], replace=False)
    if provider_id:
        _add_codes(conn, provider_id, data)
    log.info("record %s linked to facility %s", row["id"], facility_id)


def _find_facility(conn, data: Dict[str, Any]) -> Optional[str]:
    if data.get("phone"):
        hit = conn.execute("select facility_id from contacts where value = %s and facility_id is not null "
                           "limit 1", (data["phone"],)).fetchone()
        if hit:
            return hit["facility_id"]
    name = normalize.search_name(data.get("name_ar") or data.get("name_en"))
    if not (name and data.get("lat") is not None and data.get("lng") is not None):
        return None
    nearby = conn.execute(
        "select id, name_ar, name_en from facilities "
        "where geo is not null and st_dwithin(geo, st_setsrid(st_makepoint(%s, %s), 4326)::geography, %s) "
        "order by st_distance(geo, st_setsrid(st_makepoint(%s, %s), 4326)::geography) limit 10",
        (data["lng"], data["lat"], DUPLICATE_DISTANCE_M, data["lng"], data["lat"])).fetchall()
    for f in nearby:
        for other in (f["name_ar"], f["name_en"]):
            if other and difflib.SequenceMatcher(None, name, normalize.search_name(other)).ratio() >= DUPLICATE_NAME_RATIO:
                return f["id"]
    return None


def _create_facility(conn, data: Dict[str, Any]) -> str:
    name_ar = data.get("name_ar")
    if not name_ar and not data.get("name_en") and data.get("provider_name_ar"):
        name_ar = "عيادة " + data["provider_name_ar"]
    return conn.execute(
        "insert into facilities (name_ar, name_en, facility_type, governorate_code, address_ar, address_en, geo) "
        "values (%s, %s, %s, %s, %s, %s, " + _GEO_SQL + ") returning id",
        (name_ar, data.get("name_en"), data.get("facility_type") or "center", data.get("governorate_code"),
         data.get("address_ar"), data.get("address_en"),
         data.get("lng"), data.get("lat"), data.get("lng"), data.get("lat"))).fetchone()["id"]


_GEO_SQL = ("case when %s::float8 is null or %s::float8 is null then null "
            "else st_setsrid(st_makepoint(%s, %s), 4326)::geography end")


def _find_or_create_provider(conn, data: Dict[str, Any], facility_id: str) -> Optional[str]:
    name_ar = data.get("provider_name_ar")
    if not name_ar:
        return None
    name_search = normalize.search_name(name_ar)
    hit = conn.execute("select id from providers where name_search = %s limit 1", (name_search,)).fetchone()
    if hit:
        provider_id = hit["id"]
    else:
        provider_id = conn.execute(
            "insert into providers (slug, type, name_ar, name_en, name_search, gender, first_seen_at, last_seen_at) "
            "values (%s, %s, %s, %s, %s, %s, now(), now()) returning id",
            (_slug(data.get("provider_name_en") or name_ar), data.get("provider_type") or "doctor", name_ar,
             data.get("provider_name_en"), name_search, data.get("gender"))).fetchone()["id"]
    conn.execute("insert into provider_locations (provider_id, facility_id) values (%s, %s) "
                 "on conflict do nothing", (provider_id, facility_id))
    return provider_id


def _slug(name: str) -> str:
    base = "-".join(normalize.search_name(name).split())[:60] or "provider"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _add_codes(conn, provider_id: str, data: Dict[str, Any]) -> None:
    for code in data.get("specialties") or []:
        conn.execute("insert into provider_specialties (provider_id, specialty_code) "
                     "select %s, code from specialties where code = %s on conflict do nothing", (provider_id, code))
    for code in data.get("services") or []:
        conn.execute("insert into provider_services (provider_id, service_code) "
                     "select %s, code from services where code = %s on conflict do nothing", (provider_id, code))


def _replace_contacts(conn, row: Dict[str, Any], field: str, value: Optional[str], replace: bool = True) -> None:
    kinds = CONTACT_KINDS[field]
    if replace:
        conn.execute("delete from contacts where facility_id = %s and source_id = %s and kind = any(%s)",
                     (row["facility_id"], row["source_id"], list(kinds)))
    if not value:
        return
    kind = ("mobile" if normalize.is_mobile(value) else "phone") if field == "phone" else field
    exists = conn.execute("select 1 from contacts where facility_id = %s and value = %s",
                          (row["facility_id"], value)).fetchone()
    if not exists:
        conn.execute("insert into contacts (facility_id, kind, value, source_id) values (%s, %s, %s, %s)",
                     (row["facility_id"], kind, value, row["source_id"]))


def _set_facility_status(conn, row: Dict[str, Any], status: str, only_from=LIVE_STATUSES) -> None:
    hit = conn.execute("update facilities set status = %s where id = %s and status = any(%s) "
                       "and status <> %s returning id",
                       (status, row["facility_id"], list(only_from), status)).fetchone()
    if hit:
        conn.execute("insert into change_events (facility_id, field, new_value, review_status, risk, "
                     "source_id, source_record_id) values (%s, 'status', %s, 'applied', 'low', %s, %s)",
                     (row["facility_id"], Jsonb(status), row["source_id"], row["id"]))


def apply_change(conn, row: Dict[str, Any], field: str, value: Any) -> None:
    """Write one field of a source record onto its facility or provider."""
    facility_id, provider_id = row["facility_id"], row["provider_id"]
    if field in FACILITY_COLUMNS:
        if value is None and field == "facility_type":
            return
        conn.execute(f"update facilities set {field} = %s where id = %s", (value, facility_id))
    elif field in ("lat", "lng"):
        lat, lng = row["data"].get("lat"), row["data"].get("lng")
        conn.execute("update facilities set geo = " + _GEO_SQL + " where id = %s",
                     (lng, lat, lng, lat, facility_id))
    elif field in CONTACT_KINDS:
        _replace_contacts(conn, row, field, value)
    elif field == "business_status":
        if value == "CLOSED_PERMANENTLY":
            _set_facility_status(conn, row, "closed")
        elif value == "OPERATIONAL":
            _set_facility_status(conn, row, "active", only_from=("closed", "possibly_stale"))
    elif provider_id and field in ("provider_name_ar", "provider_name_en", "gender", "provider_type"):
        if field == "provider_name_ar":
            if value:
                conn.execute("update providers set name_ar = %s, name_search = %s where id = %s",
                             (value, normalize.search_name(value), provider_id))
        else:
            column = {"provider_name_en": "name_en", "gender": "gender", "provider_type": "type"}[field]
            if value or column != "type":
                conn.execute(f"update providers set {column} = %s where id = %s", (value, provider_id))
    elif provider_id and field in ("specialties", "services"):
        _add_codes(conn, provider_id, {field: value})
    # city, notes_ar, google_maps_url: kept in observations only.


def _observe(conn, row: Dict[str, Any], field: str, value: Any) -> None:
    conn.execute("insert into observations (provider_id, facility_id, source_id, source_url, field, value, "
                 "source_record_id) values (%s, %s, %s, %s, %s, %s, %s)",
                 (row["provider_id"], row["facility_id"], row["source_id"], row["source_url"], field,
                  Jsonb(value), row["id"]))
