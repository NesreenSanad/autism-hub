export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Call the API as the signed-in admin; an expired token is refreshed once. */
export async function api<T>(path: string, token: string, init: RequestInit = {}, retried = false): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_URL || "(NEXT_PUBLIC_API_URL not set)"}`);
  }
  if (resp.status === 401 && !retried) {
    const { refreshSession } = await import("@/lib/session");
    const next = await refreshSession();
    if (next) return api<T>(path, next.access_token, init, true);
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export type PlaceData = {
  name_ar?: string; name_en?: string; facility_type?: string; governorate_code?: string; city?: string;
  address_ar?: string; address_en?: string; lat?: number; lng?: number; phone?: string; whatsapp?: string;
  website?: string; facebook?: string; specialties?: string[]; services?: string[];
  provider_name_ar?: string; provider_name_en?: string;
};

export type Place = {
  id: number; source: string; external_id: string; source_url: string | null; data: PlaceData;
  review_status: "pending" | "approved" | "rejected"; facility_id: string | null;
  first_seen_at: string; last_seen_at: string; missed_runs: number;
};

export type Change = {
  id: number; field: string; old_value: unknown; new_value: unknown; risk: string | null;
  detected_at: string; facility_id: string | null; facility_name_ar: string | null;
  facility_name_en: string | null; source: string | null; source_url: string | null;
};

export type Summary = {
  pending_places: number; pending_changes: number; listed_places: number;
  last_run: { started_at: string; finished_at: string | null; status: string; error: string | null } | null;
};

export type Page<T> = { total: number; items: T[] };
