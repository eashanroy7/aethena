import type {
  ExperimentPlan,
  ExperimentPlanRequest,
  HypothesesRequest,
  HypothesesResult,
  LiteratureQCResult,
  PaperDetail,
  PaperPreview,
} from "./types";

export async function fetchLiteratureQC(
  question: string
): Promise<LiteratureQCResult> {
  const res = await fetch("/api/literature-qc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "Literature QC failed");
  }
  return res.json();
}

export async function fetchExperimentPlan(
  payload: ExperimentPlanRequest
): Promise<ExperimentPlan> {
  const res = await fetch("/api/experiment-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "Plan generation failed");
  }
  return res.json();
}

export async function fetchHypotheses(
  payload: HypothesesRequest
): Promise<HypothesesResult> {
  const res = await fetch("/api/hypotheses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "Hypotheses generation failed");
  }
  return res.json();
}

interface StreamStartPayload {
  query: string;
  total: number;
  search_queries?: string[];
  papers: Array<{
    pmcid: string;
    title: string;
    source_url: string;
  }>;
}

interface Agent1QueriesPayload {
  user_query: string;
  primary: string;
  alternates: string[];
  rationale: string;
}

interface Agent1SkippedPayload {
  message: string;
  user_query: string;
}

interface StreamProgressPayload {
  done: number;
  total: number;
}

interface StreamErrorPayload {
  message: string;
}

interface StreamPaperErrorPayload {
  pmcid: string;
  title: string;
  message: string;
  done: number;
  total: number;
}

interface StreamHandlers {
  onAgent1Start?: (payload: { message: string }) => void;
  onAgent1Queries?: (payload: Agent1QueriesPayload) => void;
  onAgent1Skipped?: (payload: Agent1SkippedPayload) => void;
  onCandidates?: (payload: StreamStartPayload) => void;
  onPaper?: (payload: PaperPreview) => void;
  onProgress?: (payload: StreamProgressPayload) => void;
  onPaperError?: (payload: StreamPaperErrorPayload) => void;
  onDone?: (payload: StreamProgressPayload) => void;
  onError?: (payload: StreamErrorPayload) => void;
}

export function startPaperStream(
  query: string,
  n: number,
  handlers: StreamHandlers
): () => void {
  if (typeof window === "undefined") {
    throw new Error("Paper stream must run in the browser.");
  }

  const params = new URLSearchParams({ query, n: String(n) });
  const source = new EventSource(`/api/papers/stream?${params.toString()}`);

  source.addEventListener("agent1_start", (event) => {
    const payload = JSON.parse(event.data) as { message: string };
    handlers.onAgent1Start?.(payload);
  });

  source.addEventListener("agent1_queries", (event) => {
    const payload = JSON.parse(event.data) as Agent1QueriesPayload;
    handlers.onAgent1Queries?.(payload);
  });

  source.addEventListener("agent1_skipped", (event) => {
    const payload = JSON.parse(event.data) as Agent1SkippedPayload;
    handlers.onAgent1Skipped?.(payload);
  });

  source.addEventListener("candidates", (event) => {
    const payload = JSON.parse(event.data) as StreamStartPayload;
    handlers.onCandidates?.(payload);
  });

  source.addEventListener("paper", (event) => {
    const payload = JSON.parse(event.data) as PaperPreview;
    handlers.onPaper?.(payload);
  });

  source.addEventListener("progress", (event) => {
    const payload = JSON.parse(event.data) as StreamProgressPayload;
    handlers.onProgress?.(payload);
  });

  source.addEventListener("paper_error", (event) => {
    const payload = JSON.parse(event.data) as StreamPaperErrorPayload;
    handlers.onPaperError?.(payload);
  });

  source.addEventListener("done", (event) => {
    const payload = JSON.parse(event.data) as StreamProgressPayload;
    handlers.onDone?.(payload);
    source.close();
  });

  source.addEventListener("error", (event: MessageEvent) => {
    if (event.data) {
      try {
        const payload = JSON.parse(event.data) as StreamErrorPayload;
        handlers.onError?.(payload);
      } catch {
        handlers.onError?.({ message: "Paper stream failed." });
      }
    } else {
      handlers.onError?.({ message: "Paper stream disconnected." });
    }
    source.close();
  });

  return () => source.close();
}

export async function fetchPaperDetail(pmcid: string): Promise<PaperDetail> {
  const res = await fetch(`/api/papers/${encodeURIComponent(pmcid)}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "Failed to load paper detail");
  }
  return res.json();
}
