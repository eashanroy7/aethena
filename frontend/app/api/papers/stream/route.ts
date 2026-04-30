// Agent1 fans out to OpenAlex + PMC for up to 50 papers and streams them as
// they arrive — the whole stream can run for the better part of a minute.
export const maxDuration = 60;

function backendBase(): string | null {
  const base =
    process.env.BACKEND_URL?.trim() ||
    process.env.NEXT_PUBLIC_BACKEND_URL?.trim() ||
    "http://127.0.0.1:8000";
  const normalized = base.replace(/\/$/, "");
  return normalized || null;
}

export async function GET(req: Request) {
  const base = backendBase();
  if (!base) {
    return new Response("BACKEND_URL is not configured.", { status: 503 });
  }

  const url = new URL(req.url);
  const query = url.searchParams.get("query");
  const n = url.searchParams.get("n") ?? "10";
  if (!query || query.trim().length < 3) {
    return new Response("query must be at least 3 characters", { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${base}/api/papers/stream?query=${encodeURIComponent(query)}&n=${encodeURIComponent(n)}`,
      {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        cache: "no-store",
      }
    );
  } catch {
    return new Response(
      `Cannot reach backend at ${base}. Start backend server or update BACKEND_URL.`,
      { status: 502 }
    );
  }

  if (!upstream.ok || !upstream.body) {
    const message = await upstream.text();
    return new Response(message || "Upstream paper stream failed.", {
      status: upstream.status || 502,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
