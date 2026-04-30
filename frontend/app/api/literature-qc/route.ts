import { NextResponse } from "next/server";
import { tryBackendJson } from "@/lib/backend-proxy";
import type { LiteratureQCRequest, LiteratureQCResult } from "@/lib/types";

// Lit QC fans out to OpenAlex + PMC + OpenAI; needs longer than Vercel's
// 10 s default. Hobby tier allows up to 60 s for Node.js serverless functions.
export const maxDuration = 60;

export async function POST(req: Request) {
  let body: LiteratureQCRequest;
  try {
    body = (await req.json()) as LiteratureQCRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!body?.question || typeof body.question !== "string") {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const remote = await tryBackendJson<LiteratureQCResult>(
    "/api/literature-qc",
    body
  );
  if (!remote.ok) {
    return NextResponse.json(
      {
        error: remote.message,
        backendUrl: remote.backendUrl,
        backendStatus: remote.status,
      },
      { status: 502 }
    );
  }
  return NextResponse.json(remote.data);
}
