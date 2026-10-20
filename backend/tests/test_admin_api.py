"""Admin review API against the test database, with Supabase Auth mocked."""
import pytest
from fastapi.testclient import TestClient

from app import auth
from collector import store
from tests.test_collector_db import osm_element, use_test_database


@pytest.fixture
def client(conn, monkeypatch):
    use_test_database(monkeypatch, SUPABASE_URL="https://x.supabase.co", SUPABASE_ANON_KEY="anon",
                      ADMIN_EMAILS="Admin@Example.org")
    monkeypatch.setattr(auth, "_user_email",
                        lambda token: {"good": "admin@example.org", "other": "someone@example.org"}.get(token))
    from app.main import app
    return TestClient(app)


ADMIN = {"Authorization": "Bearer good"}


def add_place(conn, osm_id, name):
    from collector import osm
    source = store.source_id(conn, osm.SOURCE_NAME, osm.SOURCE_URL)
    external_id, record, url = osm.to_record(osm_element(osm_id, name, phone=f"+20 2 2222 33{osm_id:02d}"), "cairo")
    store.save_record(conn, source, external_id, record, url)
    return conn.execute("select id from source_records where external_id = %s", (external_id,)).fetchone()["id"]


def test_admin_requires_an_admin_login(client):
    assert client.get("/admin/places").status_code == 401
    assert client.get("/admin/places", headers={"Authorization": "Bearer expired"}).status_code == 401
    assert client.get("/admin/places", headers={"Authorization": "Bearer other"}).status_code == 403


def test_admin_not_set_up(client, monkeypatch):
    use_test_database(monkeypatch, ADMIN_EMAILS="")
    assert client.get("/admin/places", headers=ADMIN).status_code == 503


def test_approve_lists_the_place_and_reject_hides_it(client, conn):
    first, second = add_place(conn, 1, "مركز التخاطب"), add_place(conn, 2, "مركز النور للتوحد")

    body = client.get("/admin/places", headers=ADMIN).json()
    assert body["total"] == 2 and {i["id"] for i in body["items"]} == {first, second}
    assert body["items"][0]["source"] == "openstreetmap" and body["items"][0]["data"]["governorate_code"] == "cairo"
    assert client.get("/admin/places", params={"q": "النور"}, headers=ADMIN).json()["total"] == 1

    approved = client.post(f"/admin/places/{first}/review", json={"decision": "approved"}, headers=ADMIN).json()
    assert approved["review_status"] == "approved" and approved["facility_id"]
    fac = conn.execute("select name_ar, status from facilities where id = %s", (approved["facility_id"],)).fetchone()
    assert fac == {"name_ar": "مركز التخاطب", "status": "active"}

    rejected = client.post(f"/admin/places/{second}/review", json={"decision": "rejected"}, headers=ADMIN).json()
    assert rejected["review_status"] == "rejected" and rejected["facility_id"] is None
    assert client.get("/admin/places", headers=ADMIN).json()["total"] == 0
    assert client.get("/admin/summary", headers=ADMIN).json()["listed_places"] == 1

    # A listed place can't be rejected here, and unknown ids are 404.
    resp = client.post(f"/admin/places/{first}/review", json={"decision": "rejected"}, headers=ADMIN)
    assert resp.status_code == 409
    assert client.post("/admin/places/999999/review", json={"decision": "approved"}, headers=ADMIN).status_code == 404
    assert client.post(f"/admin/places/{first}/review", json={"decision": "maybe"}, headers=ADMIN).status_code == 422


def test_review_risky_changes(client, conn):
    from collector import osm
    record_id = add_place(conn, 1, "مركز التخاطب")
    client.post(f"/admin/places/{record_id}/review", json={"decision": "approved"}, headers=ADMIN)
    source = store.source_id(conn, osm.SOURCE_NAME)
    for phone in ("+20 2 5555 6666",):
        _, record, url = osm.to_record(osm_element(1, "مركز التخاطب", phone=phone), "cairo")
        store.save_record(conn, source, "node/1", record, url)

    changes = client.get("/admin/changes", headers=ADMIN).json()
    assert changes["total"] >= 1 and changes["items"][0]["facility_name_ar"] == "مركز التخاطب"
    first = changes["items"][0]
    assert client.post(f"/admin/changes/{first['id']}/review", json={"decision": "approved"},
                       headers=ADMIN).json()["review_status"] == "applied"
    phones = {r["value"] for r in conn.execute("select value from contacts")}
    assert "+20255556666" in phones
    assert client.post(f"/admin/changes/{first['id']}/review", json={"decision": "approved"},
                       headers=ADMIN).status_code == 404
