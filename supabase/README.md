# Database (Supabase PostgreSQL)

The database runs in Supabase. This folder uses the Supabase CLI layout, so the Supabase GitHub integration applies new files in `migrations/` when they are merged into `main`.

```
config.toml   Supabase CLI project config
migrations/   schema changes and reference data, applied in filename order
```

## Rules

- Never edit a migration that has already been applied; add a new file `YYYYMMDDHHMMSS_description.sql` (or run `npx supabase migration new <description>`).
- Never commit connection strings or keys; they go in `.env`.
- Row level security is on for every table. The backend connects directly and is not affected; add policies before letting the web app query Supabase directly.

## Apply by hand (if the GitHub integration is off)

Paste each file in `migrations/` into Supabase > SQL Editor in filename order, or run `npx supabase db push` after `npx supabase link`.
