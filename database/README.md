# Database (Supabase PostgreSQL)

The database runs in Supabase. This folder holds the SQL that builds it, so every change is tracked in Git.

```
migrations/   schema changes, applied in filename order
seed/         reference data (governorates, ...)
```

## Apply

Either paste each file into Supabase > SQL Editor in order, or run them with psql:

```bash
psql "$DATABASE_URL" -f migrations/20261007000001_extensions.sql
psql "$DATABASE_URL" -f migrations/20261007000002_initial_schema.sql
psql "$DATABASE_URL" -f seed/governorates.sql
```

## Rules

- Never edit a migration that has already been applied; add a new file `YYYYMMDDHHMMSS_description.sql`.
- Never commit connection strings or keys; they go in `.env`.
- Row level security is on for every table. The backend connects directly and is not affected; add policies before letting the web app query Supabase directly.
