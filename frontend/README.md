# Frontend (Next.js)

Web app for families, in Arabic (RTL) and English. For now it has:

- `/` a "coming soon" home page.
- `/admin` the review page: sign in with your Supabase account, then approve or reject the
  places the daily collector found and the risky changes to listed places.

## Run locally

```bash
cd frontend
cp .env.example .env.local   # then fill in the three values
npm install
npm run dev                  # http://localhost:3000/admin
```

`.env.local` is ignored by Git. It only holds public values: the API address, the Supabase
URL and the anon key. Never put the service-role key or the database password here.
The backend must list `http://localhost:3000` in `CORS_ORIGINS`.

## Deploy

`npm run build` makes a static site in `out/` (no Node server needed). Upload the contents
of `out/` to any static host, for example a subdomain's folder in cPanel or Vercel, and add
that address to the backend's `CORS_ORIGINS`. The `NEXT_PUBLIC_*` values are built in, so
rebuild after changing them.
