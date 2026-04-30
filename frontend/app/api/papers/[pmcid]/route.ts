function backendBase(): string | null {
  const base =
    process.env.BACKEND_URL?.trim() ||
    process.env.NEXT_PUBLIC_BACKEND_URL?.trim() ||
    "http://127.0.0.1:8000";
  const normalized = base.replace(/\/$/, "");
  return normalized || null;
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ pmcid: string }> }
) {
  const base = backendBase();
  if (!base) {
    return new Response("BACKEND_URL is not configured.", { status: 503 });
  }

  const { pmcid } = await ctx.params;
  let upstream: Response;
  try {
    upstream = await fetch(`${base}/api/papers/${encodeURIComponent(pmcid)}`, {
      method: "GET",
      cache: "no-store",
    });
  } catch {
    return new Response(
      `Cannot reach backend at ${base}. Start backend server or update BACKEND_URL.`,
      { status: 502 }
    );
  }

  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") ?? "application/json",
    },
  });
}
