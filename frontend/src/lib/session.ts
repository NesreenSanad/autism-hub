import { API_URL, ApiError } from "@/lib/api";

/** The admin's sign-in, issued by our API (which checks it with Supabase). */
export type Session = { access_token: string; refresh_token: string | null; email: string };

const KEY = "tawasol-admin-session";
const listeners = new Set<(s: Session | null) => void>();

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function setSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {}
  listeners.forEach((fn) => fn(s));
}

export function onSessionChange(fn: (s: Session | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function post(path: string, body: unknown, token?: string): Promise<Response> {
  try {
    return await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_URL || "(NEXT_PUBLIC_API_URL not set)"}`);
  }
}

export async function signIn(email: string, password: string): Promise<void> {
  const resp = await post("/auth/login", { email, password });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `the API answered ${resp.status} ${resp.statusText}`;
    throw new ApiError(resp.status, `Sign-in failed: ${detail} (${API_URL}/auth/login)`);
  }
  setSession(body as Session);
}

/** Swap the refresh token for a new access token; null if the sign-in is over. */
export async function refreshSession(): Promise<Session | null> {
  const current = getSession();
  if (!current?.refresh_token) return null;
  const resp = await post("/auth/refresh", { refresh_token: current.refresh_token }).catch(() => null);
  const next = resp?.ok ? ((await resp.json()) as Session) : null;
  setSession(next);
  return next;
}

export async function signOut(): Promise<void> {
  const current = getSession();
  setSession(null);
  if (current) await post("/auth/logout", {}, current.access_token).catch(() => {});
}
