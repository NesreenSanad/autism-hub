# Tawasol

A tool for families of autistic children in Egypt. The first feature is a daily-updated directory of autism doctors, therapists and centres, in Arabic and English.

## Repository layout

| Folder | What it holds |
|---|---|
| `backend/` | FastAPI API and the daily doctor-check job. Deploys on MochaHost cPanel ("Setup Python App", Passenger). |
| `frontend/` | Next.js web app (PWA, Arabic RTL + English). |
| `mobile/` | Expo / React Native app (added later). |
| `database/` | Supabase SQL migrations (PostGIS, pgvector, tables) and seed data. |
| `docs/` | Plans and the provider schema. |

The database itself lives in Supabase. `database/` only holds the scripts that build it, so its history is tracked in Git.

## Secrets

Never commit credentials. Copy `.env.example` to `.env` (ignored by Git) and fill in the Supabase connection string there, or set the same variables in cPanel's Python App environment.

## Getting started

- Backend: see [backend/README.md](backend/README.md)
- Database: see [database/README.md](database/README.md)
- Web: see [frontend/README.md](frontend/README.md)
