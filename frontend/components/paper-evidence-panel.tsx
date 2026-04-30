"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, ImageIcon, Loader2, Search, Table2 } from "lucide-react";
import { fetchPaperDetail, startPaperStream } from "@/lib/api-client";
import type { PaperDetail, PaperPreview } from "@/lib/types";

type PaperRow = {
  pmcid: string;
  title: string;
  source_url: string;
  preview: string;
  fetched_at: string;
  figure_count?: number;
  table_count?: number;
  status: "pending" | "ready" | "error";
  error?: string;
};

const DEFAULT_N = 10;

export function PaperEvidencePanel({
  externalQuery,
  autoStartKey,
}: {
  externalQuery?: string;
  autoStartKey?: number;
}) {
  const [query, setQuery] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [agent1Phase, setAgent1Phase] = useState<
    "idle" | "reasoning" | "skipped"
  >("idle");
  const [agent1Message, setAgent1Message] = useState<string | null>(null);
  const [agent1Queries, setAgent1Queries] = useState<{
    primary: string;
    alternates: string[];
    rationale: string;
  } | null>(null);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [rows, setRows] = useState<PaperRow[]>([]);
  const [selectedPmcid, setSelectedPmcid] = useState<string | null>(null);
  const [selectedPaper, setSelectedPaper] = useState<PaperDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopStreamRef = useRef<(() => void) | null>(null);
  const lastAutoStartRef = useRef<number>(-1);

  useEffect(
    () => () => {
      stopStreamRef.current?.();
      stopStreamRef.current = null;
    },
    []
  );

  const effectiveQuery = query.trim().length > 0 ? query : externalQuery ?? "";
  const canStartEffective = effectiveQuery.trim().length >= 3 && !streaming;

  const hasReadyRows = useMemo(
    () => rows.some((row) => row.status === "ready"),
    [rows]
  );

  function applyPaper(row: PaperPreview) {
    setRows((prev) =>
      prev.map((item) =>
        item.pmcid === row.pmcid
          ? {
              ...item,
              preview: row.preview,
              fetched_at: row.fetched_at,
              figure_count: row.figure_count ?? 0,
              table_count: row.table_count ?? 0,
              status: "ready",
              error: undefined,
            }
          : item
      )
    );
  }

  function applyPaperError(
    pmcid: string,
    message: string
  ) {
    setRows((prev) =>
      prev.map((item) =>
        item.pmcid === pmcid ? { ...item, status: "error", error: message } : item
      )
    );
  }

  function stopStream() {
    stopStreamRef.current?.();
    stopStreamRef.current = null;
    setStreaming(false);
  }

  function onStartFetch(nextQuery?: string) {
    const clean = (nextQuery ?? effectiveQuery).trim();
    if (!clean || clean.length < 3) {
      setError("Please enter at least 3 characters.");
      return;
    }

    stopStream();
    setError(null);
    setSelectedPmcid(null);
    setSelectedPaper(null);
    setRows([]);
    setProgress({ done: 0, total: 0 });
    setAgent1Phase("idle");
    setAgent1Message(null);
    setAgent1Queries(null);
    setStreaming(true);

    stopStreamRef.current = startPaperStream(clean, DEFAULT_N, {
      onAgent1Start: (payload) => {
        setAgent1Phase("reasoning");
        setAgent1Message(payload.message);
      },
      onAgent1Queries: (payload) => {
        setAgent1Phase("idle");
        setAgent1Message(null);
        setAgent1Queries({
          primary: payload.primary,
          alternates: payload.alternates ?? [],
          rationale: payload.rationale ?? "",
        });
      },
      onAgent1Skipped: (payload) => {
        setAgent1Phase("skipped");
        setAgent1Message(payload.message);
        setAgent1Queries(null);
      },
      onCandidates: (payload) => {
        setProgress({ done: 0, total: payload.total });
        setRows(
          payload.papers.map((paper) => ({
            pmcid: paper.pmcid,
            title: paper.title,
            source_url: paper.source_url,
            preview: "",
            fetched_at: "",
            status: "pending",
          }))
        );
      },
      onPaper: (paper) => {
        applyPaper(paper);
      },
      onProgress: (next) => {
        setProgress(next);
      },
      onPaperError: (paperError) => {
        applyPaperError(paperError.pmcid, paperError.message);
        setProgress({ done: paperError.done, total: paperError.total });
      },
      onDone: (next) => {
        setProgress(next);
        setStreaming(false);
        setAgent1Phase("idle");
        setAgent1Message(null);
        /* keep agent1Queries for display until the next run */
      },
      onError: (nextError) => {
        setError(nextError.message || "Paper stream failed.");
        setStreaming(false);
        setAgent1Phase("idle");
        setAgent1Message(null);
      },
    });
  }

  useEffect(() => {
    if (autoStartKey == null) return;
    if (lastAutoStartRef.current === autoStartKey) return;
    lastAutoStartRef.current = autoStartKey;
    if (!externalQuery || externalQuery.trim().length < 3) return;
    setTimeout(() => onStartFetch(externalQuery), 0);
    // intentionally no deps on onStartFetch to avoid re-trigger loops
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStartKey, externalQuery]);

  async function onOpenDetail(row: PaperRow) {
    if (row.status !== "ready") return;
    setSelectedPmcid(row.pmcid);
    setDetailLoading(true);
    setError(null);
    try {
      const detail = await fetchPaperDetail(row.pmcid);
      setSelectedPaper(detail);
    } catch (detailError) {
      setSelectedPaper(null);
      setError(
        detailError instanceof Error
          ? detailError.message
          : "Failed to load paper detail."
      );
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <section className="interactive-surface rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-[var(--shadow)] sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold text-[var(--foreground)]">
            Evidence papers
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Agent 1 (OpenAI) rewrites your question for search, then OpenAlex {"->"} PMC
            fetch {"->"} cleaned text in SQLite. Results stream as they arrive.
          </p>
        </div>
        <span className="rounded-full border border-[var(--border)] bg-[var(--input-bg)] px-3 py-1 text-xs text-[var(--muted)]">
          Delay: 200ms per paper
        </span>
      </div>

      <div className="mt-4 flex flex-col gap-3 sm:flex-row">
        <label htmlFor="paper-query" className="sr-only">
          Paper search query
        </label>
        <input
          id="paper-query"
          value={effectiveQuery}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search open-access papers (e.g. alpha7 nicotinic receptor assembly)"
          className="h-11 w-full rounded-xl border border-[var(--border)] bg-[var(--input-bg)] px-4 text-sm text-[var(--foreground)] placeholder:text-zinc-500 focus:border-emerald-500/40 focus:outline-none focus:ring-4 focus:ring-emerald-500/15"
        />
        <button
          type="button"
          disabled={!canStartEffective}
          onClick={() => onStartFetch()}
          className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {streaming ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Search className="h-4 w-4" aria-hidden />
          )}
          {streaming ? "Fetching..." : "Fetch papers"}
        </button>
      </div>

      {agent1Phase === "reasoning" && (
        <p className="mt-3 inline-flex items-center gap-2 text-xs text-emerald-200/90">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {agent1Message ?? "Agent 1 is reasoning…"}
        </p>
      )}
      {agent1Phase === "skipped" && agent1Message && (
        <p className="mt-3 text-xs text-amber-200/90">{agent1Message}</p>
      )}
      {agent1Queries && (
        <div className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-xs leading-relaxed text-zinc-300">
          <p className="font-medium text-emerald-200/95">Search strategy (Agent 1)</p>
          {agent1Queries.rationale && (
            <p className="mt-1 text-[var(--muted)]">{agent1Queries.rationale}</p>
          )}
          <p className="mt-2">
            <span className="text-zinc-500">Primary: </span>
            {agent1Queries.primary}
          </p>
          {agent1Queries.alternates.length > 0 && (
            <p className="mt-1">
              <span className="text-zinc-500">Alternates: </span>
              {agent1Queries.alternates.join(" · ")}
            </p>
          )}
        </div>
      )}
      <p className="mt-3 text-xs text-[var(--muted)]">
        {progress.total > 0
          ? `Fetched ${progress.done}/${progress.total}...`
          : "Waiting for query."}
      </p>

      {error && (
        <p className="mt-3 rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          {error}
        </p>
      )}

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-2">
          {rows.length === 0 ? (
            <p className="rounded-xl border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted)]">
              Fetch to load paper list and previews.
            </p>
          ) : (
            rows.map((row) => (
              <button
                key={row.pmcid}
                type="button"
                onClick={() => onOpenDetail(row)}
                disabled={row.status !== "ready"}
                className={`w-full rounded-xl border p-3 text-left transition ${
                  selectedPmcid === row.pmcid
                    ? "border-emerald-500/40 bg-emerald-500/10"
                    : "border-[var(--border)] bg-[var(--input-bg)]"
                } ${
                  row.status === "ready"
                    ? "hover:border-emerald-500/30"
                    : "cursor-not-allowed opacity-70"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-[var(--foreground)]">
                      {row.title}
                    </p>
                    <p className="mt-1 text-xs text-[var(--muted)]">PMC{row.pmcid}</p>
                    {row.status === "ready" && (
                      <p className="mt-1 inline-flex items-center gap-3 text-[11px] text-zinc-400">
                        <span className="inline-flex items-center gap-1">
                          <ImageIcon className="h-3 w-3" aria-hidden />
                          {row.figure_count ?? 0}
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <Table2 className="h-3 w-3" aria-hidden />
                          {row.table_count ?? 0}
                        </span>
                      </p>
                    )}
                  </div>
                  {row.status === "pending" && (
                    <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-zinc-400" />
                  )}
                </div>
                <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-zinc-300">
                  {row.status === "ready"
                    ? row.preview
                    : row.status === "error"
                      ? row.error || "Failed to fetch this paper."
                      : "Fetching text..."}
                </p>
              </button>
            ))
          )}
        </div>

        <div className="rounded-xl border border-[var(--border)] bg-[var(--input-bg)] p-4">
          {!hasReadyRows ? (
            <p className="text-sm text-[var(--muted)]">
              Full text detail appears here when a fetched paper is selected.
            </p>
          ) : detailLoading ? (
            <p className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Loading full text...
            </p>
          ) : selectedPaper ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-[var(--foreground)]">
                    {selectedPaper.title}
                  </h3>
                  <p className="mt-1 text-xs text-[var(--muted)]">
                    PMC{selectedPaper.pmcid}
                  </p>
                </div>
                <a
                  href={selectedPaper.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-emerald-300 hover:text-emerald-200"
                >
                  Open source
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                </a>
              </div>
              <div className="mt-3 max-h-[22rem] overflow-auto rounded-lg border border-[var(--border)] bg-black/20 p-3">
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">
                  {selectedPaper.text}
                </p>
              </div>
              <div className="mt-4 space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                  Figures ({selectedPaper.figures.length})
                </h4>
                {selectedPaper.figures.length === 0 ? (
                  <p className="text-xs text-[var(--muted)]">
                    No figure images found for this paper.
                  </p>
                ) : (
                  <div className="grid gap-3 md:grid-cols-2">
                    {selectedPaper.figures.map((figure) => (
                      <a
                        key={`${figure.image_url}-${figure.label ?? ""}`}
                        href={figure.image_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg border border-[var(--border)] bg-black/20 p-2 transition hover:border-emerald-500/30"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={figure.image_url}
                          alt={figure.caption || figure.label || "Figure"}
                          className="h-40 w-full rounded object-cover"
                          loading="lazy"
                        />
                        <p className="mt-2 line-clamp-3 text-[11px] leading-relaxed text-zinc-300">
                          {figure.label ? `${figure.label}: ` : ""}
                          {figure.caption}
                        </p>
                      </a>
                    ))}
                  </div>
                )}
              </div>

              <div className="mt-4 space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                  Tables ({selectedPaper.tables.length})
                </h4>
                {selectedPaper.tables.length === 0 ? (
                  <p className="text-xs text-[var(--muted)]">
                    No tables found for this paper.
                  </p>
                ) : (
                  selectedPaper.tables.map((table, idx) => (
                    <div
                      key={`${table.label}-${idx}`}
                      className="overflow-x-auto rounded-lg border border-[var(--border)] bg-black/20 p-2"
                    >
                      <p className="px-1 pb-2 text-xs font-medium text-zinc-300">
                        {table.label}
                        {table.caption ? ` - ${table.caption}` : ""}
                      </p>
                      <table className="min-w-full text-left text-xs text-zinc-300">
                        <tbody>
                          {table.rows.map((row, rowIdx) => (
                            <tr key={`${table.label}-${idx}-${rowIdx}`} className="border-t border-[var(--border)]">
                              {row.map((cell, cellIdx) => (
                                <td
                                  key={`${table.label}-${idx}-${rowIdx}-${cellIdx}`}
                                  className="px-2 py-1.5 align-top"
                                >
                                  {cell}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-[var(--muted)]">
              Select a ready paper to view full cleaned text.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
