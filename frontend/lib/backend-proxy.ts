/**
 * Proxies POST JSON calls from the Next.js API routes to the FastAPI backend.
 *
 * Backend URL precedence:
 *   1. process.env.BACKEND_URL          (server-only, recommended for prod)
 *   2. process.env.NEXT_PUBLIC_BACKEND_URL  (also exposed to the browser)
 *   3. http://127.0.0.1:8000           (local dev fallback)
 *
 * The result type carries the precise failure reason so the calling route can
 * surface a useful error to the user instead of guessing at what broke.
 */
export type BackendResult<T> =
  | { ok: true; data: T }
  | { ok: false; status?: number; message: string; backendUrl: string };

function resolveBackendUrl(): string {
  const raw =
    process.env.BACKEND_URL?.trim() ||
    process.env.NEXT_PUBLIC_BACKEND_URL?.trim() ||
    "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}

export async function tryBackendJson<T>(
  path: string,
  body: unknown
): Promise<BackendResult<T>> {
  const backendUrl = resolveBackendUrl();
  const target = `${backendUrl}${path}`;

  try {
    const res = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      const detail = text.slice(0, 240) || res.statusText;
      return {
        ok: false,
        status: res.status,
        message: `Backend ${backendUrl} returned ${res.status}: ${detail}`,
        backendUrl,
      };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      message: `Could not reach backend at ${backendUrl}. ${reason}`,
      backendUrl,
    };
  }
}
