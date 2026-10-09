"""Daily doctor collector. Run from cPanel cron, for example:

    0 3 * * * cd ~/autism-hub/backend && ~/virtualenv/autism-hub/backend/3.11/bin/python -m jobs.daily_check >> ~/logs/daily_check.log 2>&1

Each run:
1. Applies what a reviewer approved in Supabase (new records, pending changes).
2. OpenStreetMap (free, unless OSM_ENABLED=false), every OSM_EVERY_DAYS days:
   searches each governorate in OSM_GOVERNORATES for autism-related clinics,
   centres and therapists. New places wait for review; places that disappear
   from OSM count as missed.
3. Google Places, optional and paid, only if GOOGLE_PLACES_API_KEY is set:
   - discovery (every GOOGLE_DISCOVERY_EVERY_DAYS days): searches autism
     queries in each governorate of GOOGLE_PLACES_GOVERNORATES; new places
     wait for review;
   - refresh: re-checks known places not checked for GOOGLE_REFRESH_EVERY_DAYS
     days, so changes and closures are caught and Google data stays fresh.
   It stops at GOOGLE_PLACES_MAX_REQUESTS requests per run.
4. Marks facilities that every source has missed STALE_AFTER_MISSES times in a
   row as 'possibly_stale'.

Add `--dry-run` to only report what is due.
See backend/collector/store.py for how records, changes and reviews work.
"""
import argparse
import logging
from datetime import timedelta

from app.config import get_settings
from collector import google_places, osm, store

log = logging.getLogger("daily_check")


def run_osm(conn, settings, stats: dict, client=None) -> None:
    last = store.last_ok_run(conn, "osm")
    if last is not None and store.now() - last < timedelta(days=settings.osm_every_days) - timedelta(hours=1):
        log.info("openstreetmap checked at %s; not due yet", last)
        return
    if settings.osm_governorates.strip().lower() == "all":
        governorates = list(osm.GOVERNORATE_ISO)
    else:
        governorates = [g.strip() for g in settings.osm_governorates.split(",") if g.strip() in osm.GOVERNORATE_ISO]

    source = store.source_id(conn, osm.SOURCE_NAME, osm.SOURCE_URL, osm.TERMS_NOTE)
    run_id = store.start_run(conn, "osm", source)
    started = conn.execute("select now() as t").fetchone()["t"]
    counts = {"new": 0, "changed": 0, "unchanged": 0, "unnamed": 0}
    searched, failed = [], []
    seen = set()
    for code, error, elements in osm.search(client or osm.OsmClient(), governorates):
        if error:
            failed.append(code)
            continue
        searched.append(code)
        for element in elements:
            external_id, record, url = osm.to_record(element, code)
            if external_id in seen:
                continue
            seen.add(external_id)
            if not (record.get("name_ar") or record.get("name_en")):
                counts["unnamed"] += 1
                continue
            counts[store.save_record(conn, source, external_id, record, url)] += 1
    counts["missing"] = store.mark_missing_since(conn, source, started, searched)
    counts["failed_governorates"] = failed
    # A run where every area failed is retried tomorrow; partial failures are just logged.
    store.finish_run(conn, run_id, counts, "all overpass requests failed" if failed and not searched else None)
    stats["osm"] = counts


def run_google(conn, settings, stats: dict) -> None:
    source = store.source_id(conn, google_places.SOURCE_NAME)
    governorates = store.governorate_matcher(conn)
    client = google_places.PlacesClient(settings.google_places_api_key, settings.google_places_max_requests)
    names = {r["code"]: r["name_ar"] for r in conn.execute("select code, name_ar from governorates")}

    last = store.last_ok_run(conn, "google_discovery")
    if last is None or store.now() - last >= timedelta(days=settings.google_discovery_every_days):
        run_id = store.start_run(conn, "google_discovery", source)
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        error = None
        try:
            for code in settings.google_places_governorate_list:
                if code not in names:
                    log.warning("unknown governorate code %r in GOOGLE_PLACES_GOVERNORATES", code)
                    continue
                for query in google_places.DEFAULT_QUERIES:
                    for place in client.search(f"{query} في {names[code]}", settings.google_places_max_pages):
                        place_id, record = google_places.to_record(place, governorates, code)
                        counts[store.save_record(conn, source, place_id, record,
                                                 record.get("google_maps_url"))] += 1
        except Exception as exc:  # keep going to the refresh and stale steps
            error = f"{type(exc).__name__}: {exc}"
            log.error("google discovery stopped: %s", error)
        counts["requests"] = client.requests_made
        store.finish_run(conn, run_id, counts, error)
        stats["google_discovery"] = counts

    run_id = store.start_run(conn, "google_refresh", source)
    counts = {"new": 0, "changed": 0, "unchanged": 0, "missing": 0}
    error = None
    due = conn.execute(
        "select id, external_id, data from source_records where source_id = %s "
        "and review_status <> 'rejected' and last_checked_at < now() - make_interval(days => %s) "
        "order by last_checked_at", (source, settings.google_refresh_every_days)).fetchall()
    try:
        for row in due:
            place = client.details(row["external_id"])
            if place is None:
                store.mark_missed(conn, row["id"])
                counts["missing"] += 1
                continue
            place_id, record = google_places.to_record(place, governorates, row["data"].get("governorate_code"))
            counts[store.save_record(conn, source, place_id, record, record.get("google_maps_url"))] += 1
    except google_places.BudgetExceeded:
        log.warning("google refresh reached the request budget; the rest are checked tomorrow")
        counts["stopped_at_budget"] = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.error("google refresh stopped: %s", error)
    counts["due"] = len(due)
    counts["requests"] = client.requests_made
    store.finish_run(conn, run_id, counts, error)
    stats["google_refresh"] = counts


def run(dry_run: bool = False) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    with store.connect(settings.database_url) as conn:
        if dry_run:
            pending = conn.execute(
                "select (select count(*) from source_records where review_status = 'pending') as records, "
                "(select count(*) from change_events where review_status = 'pending') as changes, "
                "(select count(*) from source_records where review_status = 'approved' and facility_id is null) "
                "+ (select count(*) from change_events where review_status = 'approved') as approved").fetchone()
            log.info("waiting for review: %s new records, %s changes; approved and not yet applied: %s",
                     pending["records"], pending["changes"], pending["approved"])
            log.info("openstreetmap: %s; google places: %s", "on" if settings.osm_enabled else "off",
                     "configured" if settings.google_places_api_key else "off (no API key)")
            return

        run_id = store.start_run(conn, "daily_check")
        stats: dict = {}
        error = None
        try:
            stats["reviews"] = store.apply_reviews(conn)
            if settings.osm_enabled:
                run_osm(conn, settings, stats)
            if settings.google_places_api_key:
                run_google(conn, settings, stats)
            stats["marked_stale"] = store.mark_stale(conn, settings.stale_after_misses)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            store.finish_run(conn, run_id, stats, error)
            log.info("daily check finished: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="only report what is waiting")
    run(parser.parse_args().dry_run)
