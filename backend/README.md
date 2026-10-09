# Backend (FastAPI)

```
app/
  main.py         FastAPI app
  config.py       settings from environment / .env
  db.py           Postgres (Supabase) connection
  routers/        API endpoints
collector/        doctor collector: sources, normalisation, change detection
jobs/
  daily_check.py  daily collector run (cPanel cron)
  import_csv.py   import a CSV list of providers
tests/
passenger_wsgi.py entry point for cPanel Passenger
```

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # then fill in DATABASE_URL
uvicorn app.main:app --reload
```

Open http://localhost:8000/health and http://localhost:8000/docs.

## Deploy on MochaHost (cPanel "Setup Python App")

1. Upload or `git clone` the repo to your home folder.
2. cPanel > Setup Python App > Create: Python 3.11 (or newest offered), Application root = `tawasol/backend`, startup file = `passenger_wsgi.py`, entry point = `application`.
3. Add the variables from `.env.example` under Environment variables.
4. Enter the virtualenv shown at the top of the page and run `pip install -r requirements.txt`, then Restart.
5. Add the daily job under cPanel > Cron Jobs (command is in `jobs/daily_check.py`).
   The job reads the same `.env` / environment variables; for cron, put them in `backend/.env`.

The plan has little memory, so the app opens a short connection per request and has no background workers.

## Doctor collector

Sources we use, and why only these: see `docs/doctor-directory-plan.md`. Vezeeta,
Facebook and similar sites are not scraped (their terms forbid it).

| Source | How it runs | New listings |
|---|---|---|
| OpenStreetMap (free, no key) | `jobs/daily_check.py`, daily via cron; searches every governorate in `OSM_GOVERNORATES` through the Overpass API | wait for review |
| CSV lists (NGOs, hospitals, your own) | `python -m jobs.import_csv list.csv --source "<list name>"`, by hand; add `--geocode` to look up coordinates from addresses (free, Nominatim) | go live (add `--review` to hold them) |
| Google Places API (optional, paid) | off unless `GOOGLE_PLACES_API_KEY` is set; searches weekly and re-checks known places weekly | wait for review |

OpenStreetMap data is under the ODbL licence: wherever the app shows it, it must say
"© OpenStreetMap contributors" with a link to https://www.openstreetmap.org/copyright.
OSM has fewer Egyptian clinics than Google, so CSV lists matter: every list you import fills the gaps.

CSV columns are in `collector/providers_template.csv`. Run with `--check` first to see problems without saving.

### Reviewing in Supabase (Table Editor)

- **New places from OpenStreetMap or Google:** `source_records` where `review_status = pending`. Set it to
  `approved` to list the place, or `rejected` to ignore it for good. The `data` column shows what the
  source returned and `source_url` links to it.
- **Risky changes** (phone, address, name, location, closed): `change_events` where
  `review_status = pending`. Set `approved` to apply or `rejected` to ignore.
  Low-risk changes (website, English name, services) are applied at once and logged as `applied`.

The next run applies approved items. Every value seen is also kept in `observations`
with its source and date, and each run is logged in `ingest_runs`.

A facility that all its sources stop returning for `STALE_AFTER_MISSES` checks in a row
becomes `possibly_stale`; if it comes back it becomes `active` again. A listing marked
`closed` or `removed_on_request` is never reopened by the job.

### Google Places cost (only if you turn it on)

Each search page or place check is one billed request (phone and website fields use the
"Enterprise" price tier). Three governorates x 4 queries x up to 2 pages is at most 24 search
requests a week, plus one request per known place a week. `GOOGLE_PLACES_MAX_REQUESTS` caps each run.
Set a budget alert in Google Cloud as well.

### Tests

```bash
pip install -r requirements-dev.txt
pytest tests                       # unit tests
TEST_DATABASE_URL=postgresql://... pytest tests   # also the database tests
```

The database tests need a local Postgres with PostGIS and the files in `supabase/migrations`
applied. They empty the data tables, so never point `TEST_DATABASE_URL` at Supabase.
