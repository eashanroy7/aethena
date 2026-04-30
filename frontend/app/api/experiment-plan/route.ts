import { NextResponse } from "next/server";
import { tryBackendJson } from "@/lib/backend-proxy";
import type {
  ExperimentPlan,
  ExperimentPlanRequest,
  LiteratureQCResult,
} from "@/lib/types";

// Agent2 + Agent4 OpenAI chained calls can run 20-40 s. Vercel's default of
// 10 s is far too short; Hobby tier allows up to 60 s.
export const maxDuration = 60;

export async function POST(req: Request) {
  let body: ExperimentPlanRequest;
  try {
    body = (await req.json()) as ExperimentPlanRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!body?.question || typeof body.question !== "string") {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const lit = body.literature as LiteratureQCResult | undefined;
  if (!lit || !lit.novelty) {
    return NextResponse.json(
      { error: "literature result with novelty is required" },
      { status: 400 }
    );
  }

  const remote = await tryBackendJson<ExperimentPlan>(
    "/api/experiment-plan",
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
