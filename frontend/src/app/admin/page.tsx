"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type Change, type Page, type Place, type Summary } from "@/lib/api";
import { GOVERNORATES, governorateName } from "@/lib/governorates";
import { getSession, onSessionChange, signIn, signOut, type Session } from "@/lib/session";

type Tab = "pending" | "approved" | "rejected" | "changes";
const PAGE_SIZE = 50;

export default function AdminPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSession(getSession());
    setReady(true);
    return onSessionChange(setSession);
  }, []);

  return (
    <div dir="ltr" className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="text-2xl font-bold">Tawasol admin</h1>
      {!ready ? (
        <p className="mt-6 opacity-70">Loading…</p>
      ) : session ? (
        <Dashboard token={session.access_token} email={session.email} onSignOut={signOut} />
      ) : (
        <SignIn />
      )}
    </div>
  );
}

function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email, password);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="mt-8 max-w-sm space-y-3 rounded-lg border border-black/10 p-5 dark:border-white/15">
      <h2 className="text-lg font-semibold">Sign in</h2>
      <input className={input} type="email" placeholder="Email" autoComplete="username" required
        value={email} onChange={(e) => setEmail(e.target.value)} />
      <input className={input} type="password" placeholder="Password" autoComplete="current-password" required
        value={password} onChange={(e) => setPassword(e.target.value)} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button className={`${button} bg-emerald-700 text-white`} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}

function Dashboard({ token, email, onSignOut }: { token: string; email: string; onSignOut: () => void }) {
  const [tab, setTab] = useState<Tab>("pending");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const loadSummary = useCallback(() => {
    api<Summary>("/admin/summary", token).then(setSummary).catch(setError);
  }, [token]);
  useEffect(loadSummary, [loadSummary]);

  return (
    <>
      <div className="mt-2 flex items-center justify-between text-sm">
        <span className="opacity-70">{email}</span>
        <button className="underline" onClick={onSignOut}>Sign out</button>
      </div>
      {error && <ErrorNotice error={error} />}
      {summary && (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Places to review" value={summary.pending_places} />
          <Stat label="Changes to review" value={summary.pending_changes} />
          <Stat label="Listed places" value={summary.listed_places} />
          <Stat label="Last daily run"
            value={summary.last_run ? new Date(summary.last_run.started_at).toLocaleDateString() : "—"}
            note={summary.last_run?.status === "failed" ? "failed" : undefined} />
        </div>
      )}
      <nav className="mt-6 flex flex-wrap gap-2 border-b border-black/10 dark:border-white/15">
        {(["pending", "approved", "rejected", "changes"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm capitalize ${tab === t ? "border-emerald-700 font-semibold" : "border-transparent opacity-70"}`}>
            {t === "changes" ? "Changes" : `${t} places`}
          </button>
        ))}
      </nav>
      {tab === "changes"
        ? <ChangeList token={token} onReviewed={loadSummary} />
        : <PlaceList key={tab} token={token} status={tab} onReviewed={loadSummary} />}
    </>
  );
}

function PlaceList({ token, status, onReviewed }: { token: string; status: Exclude<Tab, "changes">; onReviewed: () => void }) {
  const [q, setQ] = useState("");
  const [governorate, setGovernorate] = useState("");
  const [page, setPage] = useState<Page<Place> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (offset = 0) => {
    const params = new URLSearchParams({ status, limit: String(PAGE_SIZE), offset: String(offset) });
    if (q) params.set("q", q);
    if (governorate) params.set("governorate", governorate);
    try {
      const next = await api<Page<Place>>(`/admin/places?${params}`, token);
      setPage((prev) => (offset && prev ? { total: next.total, items: [...prev.items, ...next.items] } : next));
      setError(null);
    } catch (e) {
      setError(e as ApiError);
    }
  }, [token, status, q, governorate]);
  useEffect(() => { load(); }, [load]);

  async function review(place: Place, decision: "approved" | "rejected") {
    try {
      await api(`/admin/places/${place.id}/review`, token, { method: "POST", body: JSON.stringify({ decision }) });
      setPage((p) => p && { total: p.total - 1, items: p.items.filter((i) => i.id !== place.id) });
      onReviewed();
    } catch (e) {
      setError(e as ApiError);
    }
  }

  return (
    <section className="mt-4">
      <div className="flex flex-wrap gap-2">
        <input className={`${input} max-w-xs`} placeholder="Search name · بحث بالاسم" dir="auto"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select className={`${input} max-w-xs`} value={governorate} onChange={(e) => setGovernorate(e.target.value)}>
          <option value="">All governorates</option>
          {GOVERNORATES.map(([code, ar, en]) => <option key={code} value={code}>{en} · {ar}</option>)}
        </select>
      </div>
      {error && <ErrorNotice error={error} />}
      {page && <p className="mt-3 text-sm opacity-70">{page.total} {status} place{page.total === 1 ? "" : "s"}</p>}
      <ul className="mt-3 space-y-3">
        {page?.items.map((place) => (
          <PlaceCard key={place.id} place={place}
            actions={status === "pending" || (status === "rejected" && !place.facility_id) ? (
              <>
                <button className={`${button} bg-emerald-700 text-white`} onClick={() => review(place, "approved")}>Approve</button>
                {status === "pending" && (
                  <button className={`${button} border border-red-600 text-red-700 dark:text-red-400`}
                    onClick={() => review(place, "rejected")}>Reject</button>
                )}
              </>
            ) : null} />
        ))}
      </ul>
      {page && page.items.length < page.total && (
        <button className={`${button} mt-4 border border-black/20 dark:border-white/25`} onClick={() => load(page.items.length)}>
          Load more
        </button>
      )}
      {page && page.total === 0 && <p className="mt-6 opacity-70">Nothing here.</p>}
    </section>
  );
}

function PlaceCard({ place, actions }: { place: Place; actions: React.ReactNode }) {
  const d = place.data;
  const map = d.lat != null && d.lng != null
    ? `https://www.openstreetmap.org/?mlat=${d.lat}&mlon=${d.lng}#map=18/${d.lat}/${d.lng}` : null;
  return (
    <li className="rounded-lg border border-black/10 bg-white/60 p-4 dark:border-white/15 dark:bg-white/5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-lg font-semibold" dir="auto">{d.name_ar || d.name_en || "(no name)"}</p>
          {d.name_ar && d.name_en && <p className="opacity-80" dir="auto">{d.name_en}</p>}
          <p className="mt-1 text-sm opacity-70">
            {d.facility_type ?? "place"} · {governorateName(d.governorate_code)}{d.city ? ` · ${d.city}` : ""}
          </p>
        </div>
        {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
      </div>
      <dl className="mt-3 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
        {(d.address_ar || d.address_en) && <Field label="Address"><span dir="auto">{d.address_ar || d.address_en}</span></Field>}
        {d.phone && <Field label="Phone"><a className="underline" href={`tel:${d.phone}`} dir="ltr">{d.phone}</a></Field>}
        {d.whatsapp && <Field label="WhatsApp"><span dir="ltr">{d.whatsapp}</span></Field>}
        {d.website && <Field label="Website"><a className="underline break-all" href={d.website} target="_blank" rel="noreferrer">{d.website}</a></Field>}
        {d.facebook && <Field label="Facebook"><a className="underline break-all" href={d.facebook} target="_blank" rel="noreferrer">{d.facebook}</a></Field>}
        {d.provider_name_ar && <Field label="Doctor"><span dir="auto">{d.provider_name_ar}</span></Field>}
        {!!d.specialties?.length && <Field label="Specialties">{d.specialties.join(", ")}</Field>}
        {!!d.services?.length && <Field label="Services">{d.services.join(", ")}</Field>}
      </dl>
      <p className="mt-3 flex flex-wrap gap-x-4 text-xs opacity-70">
        <span>From {place.source}, first seen {new Date(place.first_seen_at).toLocaleDateString()}</span>
        {place.missed_runs > 0 && <span className="text-amber-700">missing from the last {place.missed_runs} run(s)</span>}
        {map && <a className="underline" href={map} target="_blank" rel="noreferrer">Map</a>}
        {place.source_url && <a className="underline" href={place.source_url} target="_blank" rel="noreferrer">Source</a>}
      </p>
    </li>
  );
}

function ChangeList({ token, onReviewed }: { token: string; onReviewed: () => void }) {
  const [page, setPage] = useState<Page<Change> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    api<Page<Change>>(`/admin/changes?limit=${PAGE_SIZE}`, token).then(setPage).catch(setError);
  }, [token]);

  async function review(change: Change, decision: "approved" | "rejected") {
    try {
      await api(`/admin/changes/${change.id}/review`, token, { method: "POST", body: JSON.stringify({ decision }) });
      setPage((p) => p && { total: p.total - 1, items: p.items.filter((i) => i.id !== change.id) });
      onReviewed();
    } catch (e) {
      setError(e as ApiError);
    }
  }

  return (
    <section className="mt-4">
      <p className="text-sm opacity-70">
        Risky changes a source reported for places already listed (phone, address, name, location, closed).
      </p>
      {error && <ErrorNotice error={error} />}
      <ul className="mt-3 space-y-3">
        {page?.items.map((c) => (
          <li key={c.id} className="rounded-lg border border-black/10 bg-white/60 p-4 dark:border-white/15 dark:bg-white/5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold" dir="auto">{c.facility_name_ar || c.facility_name_en || "(unknown place)"}</p>
                <p className="mt-1 text-sm"><span className="opacity-70">{c.field}:</span>{" "}
                  <span className="line-through opacity-70" dir="auto">{show(c.old_value)}</span>{" → "}
                  <span className="font-medium" dir="auto">{show(c.new_value)}</span></p>
                <p className="mt-1 text-xs opacity-70">
                  {c.source ?? "unknown source"}, {new Date(c.detected_at).toLocaleDateString()}
                  {c.source_url && <> · <a className="underline" href={c.source_url} target="_blank" rel="noreferrer">Source</a></>}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button className={`${button} bg-emerald-700 text-white`} onClick={() => review(c, "approved")}>Apply</button>
                <button className={`${button} border border-red-600 text-red-700 dark:text-red-400`} onClick={() => review(c, "rejected")}>Ignore</button>
              </div>
            </div>
          </li>
        ))}
      </ul>
      {page && page.total === 0 && <p className="mt-6 opacity-70">No changes waiting.</p>}
    </section>
  );
}

const show = (v: unknown) => (v == null || v === "" ? "(empty)" : typeof v === "string" ? v : JSON.stringify(v));

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 opacity-60">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: number | string; note?: string }) {
  return (
    <div className="rounded-lg border border-black/10 p-3 dark:border-white/15">
      <p className="text-xs opacity-70">{label}</p>
      <p className="text-xl font-semibold">{value}{note && <span className="ms-2 text-sm text-red-600">{note}</span>}</p>
    </div>
  );
}

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return (
    <p className={`mt-4 rounded-md p-3 text-sm ${tone === "error" ? "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200" : "bg-black/5"}`}>
      {children}
    </p>
  );
}

function ErrorNotice({ error }: { error: ApiError }) {
  const hint = error.status === 401 ? " Sign out and sign in again."
    : error.status === 403 ? " Add this email to ADMIN_EMAILS on the API server."
    : "";
  return <Notice tone="error">{error.message}{hint}</Notice>;
}

const input = "w-full rounded-md border border-black/20 bg-transparent px-3 py-2 dark:border-white/25";
const button = "rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50";
