"""Import a CSV list of providers (an NGO or hospital list, or one you checked yourself).

    cd ~/autism-hub/backend
    python -m jobs.import_csv path/to/list.csv --source "Egyptian Autistic Society" --check
    python -m jobs.import_csv path/to/list.csv --source "Egyptian Autistic Society"

Columns: see collector/providers_template.csv. Import the same list again later
under the same --source name to update it: changed rows go through the same
change detection as the daily job, and rows no longer in the file count as
missed (use --partial if the file is only part of the list).

New rows go live straight away; add --review to make them wait for review.
Add --geocode to look up coordinates for rows that have an address but no
lat/lng (free, via OpenStreetMap Nominatim; about one row per second).
"""
import argparse
import logging
import sys

from app.config import get_settings
from collector import csv_source, osm, store

log = logging.getLogger("import_csv")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import a CSV list of providers.")
    parser.add_argument("path")
    parser.add_argument("--source", required=True, help="name of the list, e.g. the NGO's name")
    parser.add_argument("--source-url", help="where the list came from")
    parser.add_argument("--review", action="store_true", help="new rows wait for review")
    parser.add_argument("--partial", action="store_true", help="do not count missing rows as missed")
    parser.add_argument("--check", action="store_true", help="only check the file, save nothing")
    parser.add_argument("--geocode", action="store_true", help="find lat/lng for rows with only an address")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")

    with store.connect(settings.database_url) as conn:
        governorates = store.governorate_matcher(conn)
        specialties = [r["code"] for r in conn.execute("select code from specialties")]
        services = [r["code"] for r in conn.execute("select code from services")]

        rows = []
        bad = 0
        for line, row in csv_source.read_rows(args.path):
            external_id, record, problems = csv_source.to_record(row, governorates, specialties, services)
            for problem in problems:
                log.warning("line %s: %s", line, problem)
            if not record:
                bad += 1
                continue
            rows.append((external_id, record, row.get("source_url") or args.source_url))
        ids = [r[0] for r in rows]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            log.error("the same id appears on more than one row: %s", ", ".join(sorted(dupes)))
            return 1
        log.info("%s usable rows, %s skipped", len(rows), bad)
        if args.check:
            return 0
        if args.geocode:
            geocode(conn, args.source, [r[1] for r in rows])

        source = store.source_id(conn, args.source, args.source_url, "manual CSV import")
        run_id = store.start_run(conn, "csv_import", source)
        started = conn.execute("select now() as t").fetchone()["t"]  # database clock, as last_seen_at
        counts = {"new": 0, "changed": 0, "unchanged": 0, "skipped": bad}
        for external_id, record, url in rows:
            counts[store.save_record(conn, source, external_id, record, url, auto_approve=not args.review)] += 1
        if not args.partial:
            counts["missing"] = store.mark_missing_since(conn, source, started)
        store.finish_run(conn, run_id, counts)
        log.info("import finished: %s", counts)
    return 0


def geocode(conn, source_name: str, records, client=None) -> None:
    """Fill lat/lng from the address. Coordinates found on an earlier import of
    the same row are reused while its address is unchanged."""
    known = {}
    for r in conn.execute("select r.data from source_records r join sources s on s.id = r.source_id "
                          "where s.name = %s and r.data ? 'lat'", (source_name,)):
        key = (r["data"].get("address_ar"), r["data"].get("address_en"), r["data"].get("city"))
        known[key] = (r["data"]["lat"], r["data"]["lng"])
    names = {r["code"]: r["name_ar"] for r in conn.execute("select code, name_ar from governorates")}
    client = client or osm.OsmClient(pause_seconds=1.1)
    found = missed = 0
    for record in records:
        address = record.get("address_ar") or record.get("address_en")
        if "lat" in record or not address:
            continue
        key = (record.get("address_ar"), record.get("address_en"), record.get("city"))
        point = known.get(key)
        if point is None:
            parts = [address, record.get("city"), names.get(record.get("governorate_code")), "مصر"]
            try:
                point = client.geocode("، ".join(p for p in parts if p))
            except Exception as exc:  # import the rows anyway, just without coordinates
                log.error("geocoding stopped: %s", exc)
                break
        if point:
            record["lat"], record["lng"] = point
            found += 1
        else:
            missed += 1
    log.info("geocoding: %s found, %s not found", found, missed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
