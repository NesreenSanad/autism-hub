from typing import Optional

from fastapi import APIRouter, Query

from app.db import get_connection

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
def list_providers(
    q: Optional[str] = Query(None, description="Search by name"),
    limit: int = Query(20, ge=1, le=100),
) -> list:
    """List active providers. Placeholder until the directory API is designed."""
    sql = (
        "select id, slug, type, name_ar, name_en, verification "
        "from providers where status = 'active' and not opt_out"
    )
    params: list = []
    if q:
        sql += " and name_search ilike %s"
        params.append(f"%{q}%")
    sql += " order by name_ar limit %s"
    params.append(limit)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
