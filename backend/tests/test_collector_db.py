"""End-to-end checks of the collector against a real Postgres with PostGIS.

    TEST_DATABASE_URL=postgresql://postgres:pg@localhost/autism pytest tests
"""
import json
import os

import httpx

from collector import google_places, osm, store
from jobs import daily_check, import_csv

CSV_HEADER = "id,name_ar,governorate,address_ar,lat,lng,phone,website,provider_name_ar,provider_type,specialties,services\n"


def write_csv(tmp_path, rows):
    path = tmp_path / "list.csv"
    path.write_text(CSV_HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return str(path)


def use_test_database(monkeypatch, **env):
    from app.config import get_settings
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def run_import(monkeypatch, conn, path, *args):
    use_test_database(monkeypatch)
    assert import_csv.main([path, "--source", "Test NGO", *args]) == 0


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


ROW_A = "a,مركز الأمل,القاهرة,مدينة نصر,30.05,31.33,01001234567,amal.org,د. منى علي,doctor,child_psychiatry,autism_diagnosis;speech"
ROW_B = "b,مركز النور,الجيزة,الدقي,30.04,31.21,0233334444,,,,,"


def test_csv_import_creates_facilities_and_detects_changes(conn, tmp_path, monkeypatch):
    run_import(monkeypatch, conn, write_csv(tmp_path, [ROW_A, ROW_B]))

    fac = one(conn, "select * from facilities where name_ar = 'مركز الأمل'")
    assert fac["governorate_code"] == "cairo" and fac["status"] == "active"
    assert one(conn, "select st_y(geo::geometry) as lat from facilities where id = %s", fac["id"])["lat"] == 30.05
    prov = one(conn, "select * from providers")
    assert prov["name_search"] == "مني علي" and prov["type"] == "doctor"
    assert one(conn, "select count(*) as n from provider_services where provider_id = %s", prov["id"])["n"] == 2
    contacts = {(r["kind"], r["value"]) for r in conn.execute(
        "select kind, value from contacts where facility_id = %s", (fac["id"],))}
    assert contacts == {("mobile", "+201001234567"), ("website", "https://amal.org")}
    assert one(conn, "select count(*) as n from observations where facility_id = %s", fac["id"])["n"] > 5

    # Same list again: new phone and website for A, B dropped from the list.
    changed = ROW_A.replace("01001234567", "01112223333").replace("amal.org", "amal.com")
    run_import(monkeypatch, conn, write_csv(tmp_path, [changed]))

    events = {r["field"]: r for r in conn.execute("select * from change_events")}
    assert events["website"]["review_status"] == "applied"
    assert events["phone"]["review_status"] == "pending" and events["phone"]["risk"] == "high"
    contacts = {r["value"] for r in conn.execute("select value from contacts where facility_id = %s", (fac["id"],))}
    assert contacts == {"+201001234567", "https://amal.com"}  # phone waits for review
    assert one(conn, "select missed_runs from source_records where external_id = 'b'")["missed_runs"] == 1

    # Reviewer approves the phone change; the next run applies it.
    conn.execute("update change_events set review_status = 'approved' where field = 'phone'")
    assert store.apply_reviews(conn)["changes_applied"] == 1
    contacts = {r["value"] for r in conn.execute("select value from contacts where facility_id = %s", (fac["id"],))}
    assert contacts == {"+201112223333", "https://amal.com"}

    # B missed three times in a row -> possibly stale; back in the list -> active.
    conn.execute("update source_records set missed_runs = 3 where external_id = 'b'")
    assert store.mark_stale(conn, 3) == 1
    assert one(conn, "select status from facilities where name_ar = 'مركز النور'")["status"] == "possibly_stale"
    run_import(monkeypatch, conn, write_csv(tmp_path, [changed, ROW_B]))
    assert one(conn, "select status from facilities where name_ar = 'مركز النور'")["status"] == "active"


def place(place_id, name, phone, status="OPERATIONAL"):
    return {
        "id": place_id, "displayName": {"text": name, "languageCode": "ar"},
        "formattedAddress": "مدينة نصر، محافظة القاهرة، مصر",
        "addressComponents": [{"longText": "محافظة القاهرة", "shortText": "محافظة القاهرة",
                               "types": ["administrative_area_level_1", "political"]}],
        "location": {"latitude": 30.0501, "longitude": 31.3301},
        "internationalPhoneNumber": phone, "googleMapsUri": f"https://maps.google.com/?cid={place_id}",
        "businessStatus": status, "types": ["health", "point_of_interest"],
    }


def fake_google(places_by_id, search_results):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        assert request.headers["X-Goog-Api-Key"] == "test-key"
        if request.url.path.endswith(":searchText"):
            return httpx.Response(200, json={"places": search_results})
        place_id = request.url.path.rsplit("/", 1)[-1]
        if place_id in places_by_id:
            return httpx.Response(200, json=places_by_id[place_id])
        return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})

    client = google_places.PlacesClient("test-key", 10, httpx.Client(transport=httpx.MockTransport(handler)))
    return client, calls


def test_google_places_review_dedupe_and_closure(conn):
    source = store.source_id(conn, google_places.SOURCE_NAME)
    govs = store.governorate_matcher(conn)
    # An existing facility from a CSV list with the same phone as a Google place.
    csv_source = store.source_id(conn, "Test NGO")
    store.save_record(conn, csv_source, "a", {"name_ar": "مركز الأمل للتوحد", "phone": "+201001234567",
                                               "facility_type": "center"}, auto_approve=True)

    results = [place("p1", "مركز الامل", "+20 10 01234567"), place("p2", "عيادة د. سارة", "+20 2 22223333")]
    client, calls = fake_google({}, results)
    for p in client.search("مركز توحد في القاهرة", max_pages=2):
        place_id, record = google_places.to_record(p, govs, None)
        assert record["governorate_code"] == "cairo"
        assert store.save_record(conn, source, place_id, record) == "new"
    assert json.loads(calls[0].content)["textQuery"] == "مركز توحد في القاهرة"

    # Google results wait for review and create nothing yet.
    assert one(conn, "select count(*) as n from facilities")["n"] == 1
    conn.execute("update source_records set review_status = 'approved' where source_id = %s", (source,))
    assert store.apply_reviews(conn)["records_linked"] == 2
    assert one(conn, "select count(*) as n from facilities")["n"] == 2  # p1 joined the CSV facility by phone
    p1 = one(conn, "select * from source_records where external_id = 'p1'")
    csv_rec = one(conn, "select * from source_records where external_id = 'a'")
    assert p1["facility_id"] == csv_rec["facility_id"]
    assert one(conn, "select facility_type from facilities where name_ar = 'عيادة د. سارة'")["facility_type"] == "clinic"

    # Refresh: p1 closed permanently (risky -> review), p2 gone from Google.
    client, _ = fake_google({"p1": place("p1", "مركز الامل", "+20 10 01234567", "CLOSED_PERMANENTLY")}, [])
    p, = [client.details("p1")]
    place_id, record = google_places.to_record(p, govs, None)
    assert store.save_record(conn, source, place_id, record) == "changed"
    assert client.details("p2") is None
    ev = one(conn, "select * from change_events where field = 'business_status'")
    assert ev["review_status"] == "pending"
    conn.execute("update change_events set review_status = 'approved' where id = %s", (ev["id"],))
    store.apply_reviews(conn)
    assert one(conn, "select status from facilities where id = %s", p1["facility_id"])["status"] == "closed"


def test_budget_stops_requests():
    client, calls = fake_google({}, [place("p1", "مركز", None)])
    client.max_requests = 1
    list(client.search("q", max_pages=1))
    try:
        client.details("p1")
    except google_places.BudgetExceeded:
        pass
    else:
        raise AssertionError("expected BudgetExceeded")
    assert len(calls) == 1


def test_daily_check_run(conn, monkeypatch):
    use_test_database(monkeypatch, GOOGLE_PLACES_API_KEY="test-key", GOOGLE_PLACES_GOVERNORATES="cairo,nowhere",
                      OSM_ENABLED="false")
    client, calls = fake_google({}, [place("p1", "مركز الامل", "+20 10 01234567")])
    monkeypatch.setattr(google_places, "PlacesClient", lambda *a, **k: client)

    daily_check.run()
    # 4 queries in Cairo, one page each; the unknown governorate is skipped.
    assert len(calls) == 4
    assert one(conn, "select review_status from source_records where external_id = 'p1'")["review_status"] == "pending"
    runs = {r["job"]: r for r in conn.execute("select * from ingest_runs")}
    assert runs["google_discovery"]["status"] == "ok" and runs["daily_check"]["status"] == "ok"
    assert runs["google_discovery"]["stats"]["new"] == 1

    # Next day: discovery not due yet, p1 not due for refresh -> no requests.
    calls.clear()
    daily_check.run()
    assert calls == []

    # A week later p1 is due for a refresh and Google no longer has it.
    conn.execute("update source_records set last_checked_at = now() - interval '8 days'")
    conn.execute("update ingest_runs set started_at = now() - interval '8 days' where job = 'google_discovery'")
    client.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"places": []}) if r.url.path.endswith(":searchText") else httpx.Response(404)))
    daily_check.run()
    assert one(conn, "select missed_runs from source_records where external_id = 'p1'")["missed_runs"] == 1


def osm_element(osm_id, name, phone="+20 2 2222 3333", lat=30.05):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": 31.33,
            "tags": {"name": name, "amenity": "clinic", "phone": phone, "healthcare:speciality": "speech_therapy"}}


class FakeOsm:
    def __init__(self, by_governorate, fail=()):
        self.by_governorate, self.fail, self.calls = by_governorate, set(fail), []

    def search_governorate(self, code):
        self.calls.append(code)
        if code in self.fail:
            raise httpx.HTTPError("busy")
        return self.by_governorate.get(code, [])

    def geocode(self, query):
        self.calls.append(query)
        return (30.1, 31.2) if "مدينة نصر" in query else None


def test_osm_daily_run(conn, monkeypatch):
    use_test_database(monkeypatch, OSM_GOVERNORATES="cairo,giza")
    from app.config import get_settings
    fake = FakeOsm({"cairo": [osm_element(1, "مركز التخاطب"), osm_element(2, "")],
                    "giza": [osm_element(3, "Speech Center Giza")]})
    stats = {}
    daily_check.run_osm(conn, get_settings(), stats, client=fake)
    assert fake.calls == ["cairo", "giza"]
    assert stats["osm"]["new"] == 2 and stats["osm"]["unnamed"] == 1
    rec = one(conn, "select * from source_records where external_id = 'node/1'")
    assert rec["review_status"] == "pending" and rec["source_url"] == "https://www.openstreetmap.org/node/1"
    assert rec["data"]["specialties"] == ["speech_therapy"] and rec["data"]["governorate_code"] == "cairo"

    # Not due again the same day.
    daily_check.run_osm(conn, get_settings(), {}, client=fake)
    assert fake.calls == ["cairo", "giza"]

    # Next day: node 1 gone from Cairo; Giza fails, so node 3 is not counted as missed.
    conn.execute("update ingest_runs set started_at = now() - interval '1 day'")
    conn.execute("update source_records set last_seen_at = now() - interval '1 day'")
    fake2 = FakeOsm({"cairo": []}, fail={"giza"})
    stats = {}
    daily_check.run_osm(conn, get_settings(), stats, client=fake2)
    assert stats["osm"]["failed_governorates"] == ["giza"] and stats["osm"]["missing"] == 1
    assert one(conn, "select missed_runs from source_records where external_id = 'node/1'")["missed_runs"] == 1
    assert one(conn, "select missed_runs from source_records where external_id = 'node/3'")["missed_runs"] == 0


def test_osm_to_record_handles_ways_and_english_names():
    element = {"type": "way", "id": 9, "center": {"lat": 31.2, "lon": 29.9},
               "tags": {"name": "Hope Autism Center", "name:ar": "مركز الأمل للتوحد",
                        "addr:street": "شارع فؤاد", "addr:city": "الإسكندرية",
                        "contact:phone": "03 4567890;012 3456 7890", "website": "hope.example"}}
    external_id, record, url = osm.to_record(element, "alexandria")
    assert external_id == "way/9" and url.endswith("/way/9")
    assert record["name_ar"] == "مركز الأمل للتوحد" and record["name_en"] == "Hope Autism Center"
    assert record["facility_type"] == "center" and record["phone"] == "+2034567890"
    assert record["address_ar"] == "شارع فؤاد، الإسكندرية" and record["lat"] == 31.2


def test_csv_geocode(conn, tmp_path, monkeypatch):
    rows = [{"name_ar": "مركز", "address_ar": "مدينة نصر", "governorate_code": "cairo"},
            {"name_ar": "مركز 2", "address_ar": "مكان مجهول"},
            {"name_ar": "مركز 3", "lat": 30.0, "lng": 31.0, "address_ar": "مدينة نصر"}]
    fake = FakeOsm({})
    import_csv.geocode(conn, "Test NGO", rows, client=fake)
    assert (rows[0]["lat"], rows[0]["lng"]) == (30.1, 31.2)
    assert "lat" not in rows[1] and len(fake.calls) == 2
    assert "القاهرة" in fake.calls[0]
