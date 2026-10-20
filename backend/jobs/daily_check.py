"""Daily doctor collector. Run from cPanel cron, for example:

    0 3 * * * cd ~/autism-hub/backend && ~/virtualenv/autism-hub/backend/3.11/bin/python -m jobs.daily_check >> ~/logs/daily_check.log 2>&1

Each run:
1. Applies what a reviewer approved in Supabase (new records, pending changes).
COLLECTOR_SOURCES picks the automatic sources: "osm" (default), "google" or
"osm,google".

2. OpenStreetMap ("osm", free), every OSM_EVERY_DAYS days (OSM_METHOD=overpass
   asks the Overpass servers; OSM_METHOD=extract downloads Egypt from Geofabrik):
   searches each governorate in OSM_GOVERNORATES for autism-related clinics,
   centres and therapists. New places wait for review; places that disappear
   from OSM count as missed.
3. Google Places ("google", paid, needs GOOGLE_PLACES_API_KEY):
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
import tempfile
from datetime import timedelta

from app.config import get_settings

from collector import google_places, osm, osm_extract, store

log = logging.getLogger("daily_check")


def run_osm(conn, settings, stats: dict, client=None) -> None:
    if settings.osm_governorates.strip().lower() == "all":
        governorates = list(osm.GOVERNORATE_ISO)
    else:
        governorates = [g.strip() for g in settings.osm_governorates.split(",") if g.strip() in osm.GOVERNORATE_ISO]
    job = "osm"
    last = store.last_ok_run(conn, "osm")
    if last is not None and store.now() - last < timedelta(days=settings.osm_every_days) - timedelta(hours=1):
        # Not due, but governorates that failed last time are tried again today.
        retry = [g for g in store.last_failed_governorates(conn) if g in governorates]
        if not retry:
            log.info("openstreetmap checked at %s; not due yet", last)
            return
        log.info("openstreetmap: retrying %s, which failed last time", ", ".join(retry))
        governorates, job = retry, "osm_retry"

    source = store.source_id(conn, osm.SOURCE_NAME, osm.SOURCE_URL, osm.TERMS_NOTE)
    run_id = store.start_run(conn, job, source)
    started = conn.execute("select now() as t").fetchone()["t"]
    counts = {"new": 0, "changed": 0, "unchanged": 0, "unnamed": 0}
    searched, failed = [], []
    seen = set()
    workdir = tempfile.TemporaryDirectory()
    if settings.osm_method.strip().lower() == "extract" and client is None:
        url = settings.osm_extract_url or osm_extract.EXTRACT_URL
        results = osm_extract.search(governorates, workdir.name, url=url)
    else:
        client = client or osm.OsmClient(urls=settings.overpass_url_list or osm.OVERPASS_URLS)
        results = osm.search(client, governorates)
    for code, error, elements in results:
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
    # Only governorates that were fully searched count places as missing; failed
    # ones are kept in the stats and retried on the next run.
    counts["missing"] = store.mark_missing_since(conn, source, started, searched)
    counts["failed_governorates"] = failed
    workdir.cleanup()
    # A run where every area failed is retried tomorrow; partial failures are just logged.
    store.finish_run(conn, run_id, counts, "all openstreetmap searches failed" if failed and not searched else None)
    stats[job] = counts


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
            log.info("sources: %s; google api key: %s", ", ".join(settings.collector_source_list) or "none",
                     "set" if settings.google_places_api_key else "not set")
            return

        run_id = store.start_run(conn, "daily_check")
        stats: dict = {}
        error = None
        try:
            stats["reviews"] = store.apply_reviews(conn)
            sources = settings.collector_source_list
            unknown = set(sources) - {"osm", "google"}
            if unknown:
                log.warning("ignoring unknown COLLECTOR_SOURCES: %s (use osm, google)", ", ".join(sorted(unknown)))
            if "osm" in sources:
                run_osm(conn, settings, stats)
            if "google" in sources:
                if settings.google_places_api_key:
                    run_google(conn, settings, stats)
                else:
                    log.warning("COLLECTOR_SOURCES includes google but GOOGLE_PLACES_API_KEY is empty; skipping Google")
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
