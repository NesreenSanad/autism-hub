# Frontend (Next.js)

Web app for families, in Arabic (RTL) and English. For now it has:

- `/` a "coming soon" home page.
- `/admin` the review page: sign in with your admin email and password, then approve or reject the
  places the daily collector found and the risky changes to listed places.

## Run locally

```bash
cd frontend
cp .env.example .env.local   # the API address
npm install
npm run dev                  # http://localhost:3000/admin
```

The web app only talks to the backend API, including for sign-in (`/auth/login`); it holds
no Supabase keys. `.env.local` only has the API address.
The backend must list `http://localhost:3000` in `CORS_ORIGINS`.

## Deploy

`npm run build` makes a static site in `out/` (no Node server needed). Upload the contents
of `out/` to any static host, for example a subdomain's folder in cPanel or Vercel, and add
that address to the backend's `CORS_ORIGINS`. The `NEXT_PUBLIC_*` values are built in, so
rebuild after changing them.
