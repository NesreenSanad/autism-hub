"""Review queue for the admin page: places the collector found, and risky
changes to places already listed."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_admin
from app.config import get_settings
from collector import store

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

RECORD_COLUMNS = (
    "r.id, s.name as source, r.external_id, r.source_url, r.data, r.review_status, r.facility_id, "
    "r.first_seen_at, r.last_seen_at, r.missed_runs"
)


class Review(BaseModel):
    decision: Literal["approved", "rejected"]


def _conn():
    url = get_settings().database_url
    if not url:
        raise HTTPException(503, "DATABASE_URL is not set")
    return store.connect(url)


@router.get("/summary")
def summary() -> dict:
    with _conn() as conn:
        counts = conn.execute(
            "select (select count(*) from source_records where review_status = 'pending') as pending_places, "
            "(select count(*) from change_events where review_status = 'pending') as pending_changes, "
            "(select count(*) from facilities where status = 'active') as listed_places").fetchone()
        last = conn.execute("select started_at, finished_at, status, stats, error from ingest_runs "
                            "where job = 'daily_check' order by started_at desc limit 1").fetchone()
    return {**counts, "last_run": last}


@router.get("/places")
def list_places(
    status: Literal["pending", "approved", "rejected"] = "pending",
    q: Optional[str] = Query(None, description="Part of the Arabic or English name"),
    governorate: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    where, params = ["r.review_status = %s"], [status]
    if q:
        where.append("(r.data->>'name_ar' ilike %s or r.data->>'name_en' ilike %s)")
        params += [f"%{q}%", f"%{q}%"]
    if governorate:
        where.append("r.data->>'governorate_code' = %s")
        params.append(governorate)
    sql_where = " and ".join(where)
    with _conn() as conn:
        total = conn.execute(f"select count(*) as n from source_records r where {sql_where}", params).fetchone()["n"]
        items = conn.execute(
            f"select {RECORD_COLUMNS} from source_records r join sources s on s.id = r.source_id "
            f"where {sql_where} order by r.first_seen_at desc, r.id desc limit %s offset %s",
            params + [limit, offset]).fetchall()
    return {"total": total, "items": items}


@router.post("/places/{record_id}/review")
def review_place(record_id: int, review: Review) -> dict:
    """Approve: the place is listed at once (linked to a matching facility or a
    new one). Reject: it is ignored from now on, also by later runs."""
    with _conn() as conn:
        with conn.transaction():
            row = conn.execute("select * from source_records where id = %s for update", (record_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Place not found")
            if review.decision == "rejected" and row["facility_id"]:
                raise HTTPException(409, "This place is already listed; it cannot be rejected here")
            conn.execute("update source_records set review_status = %s where id = %s",
                         (review.decision, record_id))
            if review.decision == "approved" and not row["facility_id"]:
                store.link_record(conn, row)
        item = conn.execute(f"select {RECORD_COLUMNS} from source_records r join sources s on s.id = r.source_id "
                            "where r.id = %s", (record_id,)).fetchone()
    return item


@router.get("/changes")
def list_changes(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    with _conn() as conn:
        total = conn.execute("select count(*) as n from change_events where review_status = 'pending'").fetchone()["n"]
        items = conn.execute(
            "select c.id, c.field, c.old_value, c.new_value, c.risk, c.detected_at, c.facility_id, "
            "f.name_ar as facility_name_ar, f.name_en as facility_name_en, s.name as source, r.source_url "
            "from change_events c left join facilities f on f.id = c.facility_id "
            "left join sources s on s.id = c.source_id left join source_records r on r.id = c.source_record_id "
            "where c.review_status = 'pending' order by c.detected_at, c.id limit %s offset %s",
            (limit, offset)).fetchall()
    return {"total": total, "items": items}


@router.post("/changes/{change_id}/review")
def review_change(change_id: int, review: Review) -> dict:
    """Approve: the new value is written to the listing now. Reject: the
    listing keeps its current value."""
    with _conn() as conn:
        with conn.transaction():
            event = conn.execute("select * from change_events where id = %s and review_status = 'pending' "
                                 "for update", (change_id,)).fetchone()
            if not event:
                raise HTTPException(404, "Change not found or already reviewed")
            if review.decision == "approved":
                row = conn.execute("select * from source_records where id = %s",
                                   (event["source_record_id"],)).fetchone()
                if row and row["facility_id"]:
                    store.apply_change(conn, row, event["field"], event["new_value"])
            conn.execute("update change_events set review_status = %s where id = %s",
                         ("applied" if review.decision == "approved" else "rejected", change_id))
    return {"id": change_id, "review_status": "applied" if review.decision == "approved" else "rejected"}
