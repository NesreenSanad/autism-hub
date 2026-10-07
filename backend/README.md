# Backend (FastAPI)

```
app/
  main.py         FastAPI app
  config.py       settings from environment / .env
  db.py           Postgres (Supabase) connection
  routers/        API endpoints
jobs/
  daily_check.py  daily provider check (cPanel cron)
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

The plan has little memory, so the app opens a short connection per request and has no background workers.
