"use client";

import {
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ArrowDown,
  ArrowLeft,
  BookOpenCheck,
  Flag,
  Loader2,
} from "lucide-react";
import {
  fetchExperimentPlan,
  fetchHypotheses,
  fetchLiteratureQC,
} from "@/lib/api-client";
import type {
  ExperimentPlan,
  HypothesisSuggestion,
  LiteratureQCResult,
  ProteinModel,
  WetLabPlannerContext,
} from "@/lib/types";
import { HeroSphere } from "./hero-sphere";
import { Markdown } from "./markdown";
import { noveltyHelp } from "./novelty-badge";
import { PaperEvidencePanel } from "./paper-evidence-panel";

const SAMPLE_QUESTIONS = [
  {
    label: "RIC-3 Folding",
    text: "Will C. elegans RIC-3 co-expression support correct folding and surface trafficking of human alpha7 nAChR in HEK293 cells at levels at least 40% higher than human RIC-3?",
  },
  {
    label: "Transmembrane stability",
    text: "Will the transmembrane domain of human alpha7 nAChR show reduced membrane insertion efficiency without RIC-3 chaperone support, measured by patch-clamp electrophysiology?",
  },
  {
    label: "GTS-21 agonism",
    text: "Will GTS-21 partial agonist activate human alpha7 nAChR with lower desensitization than acetylcholine in HEK293 cells expressing alpha7 with C. elegans RIC-3?",
  },
] as const;

type Step = "input" | "literature" | "hypotheses" | "plan";

const SECTION_IDS = [
  "overview",
  "protocol",
  "materials",
  "budget",
  "timeline",
  "validation",
] as const;

export function ExperimentWorkspace() {
  const [question, setQuestion] = useState<string>("");
  const [step, setStep] = useState<Step>("input");
  const [literature, setLiterature] = useState<LiteratureQCResult | null>(null);
  const [suggestions, setSuggestions] = useState<HypothesisSuggestion[] | null>(null);
  const [selectedSuggestion, setSelectedSuggestion] =
    useState<HypothesisSuggestion | null>(null);
  const [proteinModels, setProteinModels] = useState<ProteinModel[]>([]);
  const [plan, setPlan] = useState<ExperimentPlan | null>(null);
  const [deselectedRefs, setDeselectedRefs] = useState<Set<string>>(new Set());
  const [revealValidation, setRevealValidation] = useState(false);
  const [qcLoading, setQcLoading] = useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] =
    useState<(typeof SECTION_IDS)[number]>("overview");
  const [plannerContext, setPlannerContext] = useState<WetLabPlannerContext>({
    budgetCapUsd: 15000,
    timelineWeeks: 8,
    availableInstruments: [],
    availableMaterials: [],
    requiredMaterials: [],
    preferredAssays: ["Patch-clamp", "Radioligand binding"],
    notes: "",
  });
  const [evidenceAutoStartKey, setEvidenceAutoStartKey] = useState(0);
  const [sliderHeight, setSliderHeight] = useState<number | undefined>(undefined);
  const [litKeywords, setLitKeywords] = useState<string>("");
  const [heroMouse, setHeroMouse] = useState({ x: 0, y: 0, hovered: false });
  const slideRefs = useRef<(HTMLDivElement | null)[]>([null, null, null, null]);
  const heroRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);

  const canRunQc = question.trim().length >= 20;
  const parseList = (raw: string) =>
    raw
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  const stepIndex =
    step === "input" ? 0 : step === "literature" ? 1 : step === "hypotheses" ? 2 : 3;

  const goBack = useCallback(() => {
    if (step === "literature") setStep("input");
    else if (step === "hypotheses") setStep("literature");
    else if (step === "plan") setStep("hypotheses");
  }, [step]);

  const runLiteratureQc = useCallback(
    async (extraKeywords?: string) => {
      setError(null);
      setQcLoading(true);
      setRevealValidation(false);
      setLiterature(null);
      setSuggestions(null);
      setSelectedSuggestion(null);
      setProteinModels([]);
      setPlan(null);
      try {
        const merged = [question.trim(), extraKeywords?.trim()]
          .filter(Boolean)
          .join(" ");
        const res = await fetchLiteratureQC(merged);
        setLiterature(res);
        setDeselectedRefs(new Set());
        setTimeout(() => setRevealValidation(true), 30);
        setEvidenceAutoStartKey((k) => k + 1);
        setStep("literature");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Something went wrong");
      } finally {
        setQcLoading(false);
      }
    },
    [question]
  );

  const runHypotheses = useCallback(async () => {
    if (!literature) return;
    setError(null);
    setSuggestionsLoading(true);
    setSuggestions(null);
    setSelectedSuggestion(null);
    setProteinModels([]);
    try {
      const filteredLit = {
        ...literature,
        references: literature.references.filter(
          (r) => !deselectedRefs.has(r.url + r.title)
        ),
      };
      const res = await fetchHypotheses({
        question: question.trim(),
        literature: filteredLit,
      });
      setSuggestions(res.hypotheses);
      setProteinModels(res.proteinModels);
      setStep("hypotheses");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setSuggestionsLoading(false);
    }
  }, [question, literature, deselectedRefs]);

  const runPlan = useCallback(async () => {
    if (!literature) return;
    setError(null);
    setPlanLoading(true);
    setPlan(null);
    try {
      const res = await fetchExperimentPlan({
        question: selectedSuggestion ? selectedSuggestion.description : question.trim(),
        literature,
        proteinModels,
        plannerContext,
      });
      setPlan(res);
      setStep("plan");
      setActiveSection("overview");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setPlanLoading(false);
    }
  }, [question, literature, selectedSuggestion, proteinModels, plannerContext]);

  const reset = useCallback(() => {
    setStep("input");
    setLiterature(null);
    setDeselectedRefs(new Set());
    setSuggestions(null);
    setSelectedSuggestion(null);
    setProteinModels([]);
    setPlan(null);
    setRevealValidation(false);
    setError(null);
    setActiveSection("overview");
    setLitKeywords("");
    setPlannerContext({
      budgetCapUsd: 15000,
      timelineWeeks: 8,
      availableInstruments: [],
      availableMaterials: [],
      requiredMaterials: [],
      preferredAssays: ["Patch-clamp", "Radioligand binding"],
      notes: "",
    });
  }, []);

  const fmtMoney = useMemo(
    () => (n: number, c: string) =>
      new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: c,
        maximumFractionDigits: 0,
      }).format(n),
    []
  );

  const minimalThemeVars: CSSProperties = {
    "--border": "rgba(255,255,255,0.12)",
    "--card": "#000000",
    "--card-muted": "#000000",
    "--input-bg": "#050505",
    "--foreground": "#ffffff",
    "--muted": "#a1a1aa",
    "--header-bg": "#000000",
    "--pill-idle": "rgba(255,255,255,0.08)",
    "--shadow": "none",
  } as CSSProperties;

  useEffect(() => {
    const el = slideRefs.current[stepIndex];
    if (!el) return;
    setSliderHeight(el.scrollHeight);
    const obs = new ResizeObserver(() => setSliderHeight(el.scrollHeight));
    obs.observe(el);
    return () => obs.disconnect();
  }, [stepIndex, suggestions, proteinModels, plan]);

  useEffect(() => {
    if (!mainRef.current) return;
    const top = mainRef.current.getBoundingClientRect().top + window.scrollY - 56;
    window.scrollTo({ top, behavior: "smooth" });
  }, [stepIndex]);

  useEffect(() => {
    if (!plan) return;
    const ids = SECTION_IDS.map((id) => `section-${id}`);
    const els = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));
    if (els.length === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible?.target?.id) return;
        const key = visible.target.id.replace("section-", "") as (typeof SECTION_IDS)[number];
        if (SECTION_IDS.includes(key)) setActiveSection(key);
      },
      { rootMargin: "-42% 0px -45% 0px", threshold: [0, 0.1, 0.25, 0.5, 1] }
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [plan]);

  return (
    <div
      className="relative min-h-screen overflow-x-hidden bg-[#000000] text-white"
      style={minimalThemeVars}
    >
      <header className="sticky top-0 z-40 border-b border-white/10 bg-[#000000]/95 backdrop-blur-sm">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="flex items-center justify-between py-3">
            <div className="flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/logo.jpeg"
                alt="Aethena"
                className="h-[54px] w-[54px] rounded-lg object-contain"
                style={{ filter: "invert(1)" }}
              />
              <p className="text-[22px] font-medium tracking-[-0.02em] text-white">
                <span className="font-semibold">Aethena</span>
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs text-zinc-500">
              <span className="hidden sm:inline">alpha7 nAChR</span>
            </div>
          </div>
        </div>
      </header>

      <section
        ref={heroRef}
        className="relative flex min-h-[calc(100vh-49px)] flex-col items-center justify-center overflow-hidden px-4 py-20 text-center"
        onMouseMove={(e) => {
          if (!heroRef.current) return;
          const r = heroRef.current.getBoundingClientRect();
          setHeroMouse({
            x: ((e.clientX - r.left) / r.width) * 2 - 1,
            y: -((e.clientY - r.top) / r.height) * 2 + 1,
            hovered: true,
          });
        }}
        onMouseLeave={() => setHeroMouse({ x: 0, y: 0, hovered: false })}
      >
        <div className="pointer-events-none absolute inset-0" style={{ opacity: 0.55 }}>
          <HeroSphere
            mouseX={heroMouse.x}
            mouseY={heroMouse.y}
            isHovered={heroMouse.hovered}
          />
        </div>
        <div className="relative z-10 flex flex-col items-center">
          <div className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs tracking-wide text-zinc-400">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
            AI-native science
          </div>
          <h1 className="max-w-2xl text-[44px] font-semibold tracking-[-0.03em] text-white sm:text-[68px] sm:leading-[0.93]">
            <span
              className="block transition-transform duration-700 ease-out"
              style={{
                transform: `translate(${heroMouse.x * -18}px, ${heroMouse.y * -10}px)`,
              }}
            >
              Research question to
            </span>
            <span
              className="block text-white/70 transition-transform duration-700 ease-out"
              style={{
                transform: `translate(${heroMouse.x * -8}px, ${heroMouse.y * -5}px)`,
              }}
            >
              runnable experiment.
            </span>
          </h1>
          <p className="mt-5 text-[18px] font-light tracking-[-0.01em] text-zinc-400">
            One question. Multi-agent orchestration. Wetlab-ready plan.
          </p>
          <button
            type="button"
            onClick={() => {
              if (!mainRef.current) return;
              const top =
                mainRef.current.getBoundingClientRect().top + window.scrollY - 56;
              window.scrollTo({ top, behavior: "smooth" });
            }}
            className="mt-10 inline-flex items-center gap-2.5 rounded-2xl bg-emerald-500 px-7 py-3.5 text-[15px] font-semibold text-emerald-950 shadow-xl shadow-emerald-500/20 transition hover:bg-emerald-400 active:scale-[0.98]"
          >
            Ask a research question
            <ArrowDown className="h-4 w-4" />
          </button>
        </div>
      </section>

      <main ref={mainRef} className="relative mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <div className="relative">
          {step !== "input" && (
            <button
              type="button"
              onClick={goBack}
              aria-label="Go back"
              className="absolute -top-6 left-0 flex items-center gap-1 text-[11px] text-zinc-500 transition hover:text-white"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
            </button>
          )}
          <StepTracker
            step={step}
            stepIndex={stepIndex}
            canOpenValidation={Boolean(literature)}
            canOpenHypotheses={Boolean(suggestions)}
            canOpenPlan={Boolean(plan)}
            onOpenHypothesis={() => setStep("input")}
            onOpenValidation={() => {
              if (!literature) return;
              setStep("literature");
            }}
            onOpenHypotheses={() => {
              if (!suggestions) return;
              setStep("hypotheses");
            }}
            onOpenPlan={() => {
              if (!plan) return;
              setStep("plan");
            }}
          />
        </div>

        <div
          className="overflow-hidden transition-[height] duration-500 ease-in-out"
          style={{ height: sliderHeight }}
        >
          <div
            className="flex transition-transform duration-500 ease-in-out will-change-transform"
            style={{ transform: `translateX(-${stepIndex * 100}%)` }}
          >
            <div ref={(el) => { slideRefs.current[0] = el; }} className="w-full min-w-full shrink-0">
              <div className="w-full rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-[var(--shadow)] sm:p-6">
                <h2 className="text-xl font-semibold tracking-[-0.02em] text-white">
                  Your scientific question
                </h2>
                <label htmlFor="hypothesis" className="sr-only">Research question</label>
                <textarea
                  id="hypothesis"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={6}
                  placeholder="What are you testing? What outcome do you expect? How will you measure it?"
                  className="mt-3 w-full resize-y rounded-xl border border-[var(--border)] bg-[var(--input-bg)] px-4 py-3 text-sm leading-relaxed text-[var(--foreground)] outline-none transition placeholder:text-zinc-600 focus:border-emerald-500/40 focus:ring-4 focus:ring-emerald-500/15"
                />
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="w-full text-xs font-medium text-zinc-500">Example questions</span>
                  {SAMPLE_QUESTIONS.map((s) => (
                    <button
                      key={s.label}
                      type="button"
                      onClick={() => setQuestion(s.text)}
                      className="rounded-full border border-[var(--border)] bg-[var(--input-bg)] px-3 py-1.5 text-xs font-medium text-[var(--foreground)] transition hover:border-emerald-500/35 hover:bg-emerald-500/5"
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    disabled={!canRunQc || qcLoading}
                    onClick={() => runLiteratureQc()}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-emerald-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {qcLoading ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : (
                      <BookOpenCheck className="h-4 w-4" aria-hidden />
                    )}
                    Run literature check
                  </button>
                  {(literature || plan) && (
                    <button
                      type="button"
                      onClick={reset}
                      className="text-sm font-medium text-[var(--muted)] underline-offset-4 hover:text-[var(--foreground)] hover:underline"
                    >
                      Start over
                    </button>
                  )}
                </div>
              </div>
              {error && step === "input" && (
                <div role="alert" className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                  {error}
                </div>
              )}
            </div>

            <div ref={(el) => { slideRefs.current[1] = el; }} className="w-full min-w-full shrink-0">
              {literature && (
                <div
                  id="literature-qc"
                  className={`space-y-4 transition-all duration-500 ease-out ${
                    revealValidation ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"
                  }`}
                >
                  <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                    <h2 className="text-xl font-semibold tracking-[-0.02em] text-white">Literature</h2>
                    <p className="mt-2 text-sm text-zinc-400">{noveltyHelp(literature.novelty)}</p>
                    <p className="mt-3 text-sm leading-relaxed text-zinc-300">{literature.summary}</p>
                  </div>

                  <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                    <h3 className="text-sm font-semibold tracking-[-0.01em] text-white">Agent1 Evidence Stream</h3>
                    <p className="mt-1 text-xs text-zinc-500">
                      Fetch papers first, then Agent2 synthesizes all available sources.
                    </p>
                    <div className="mt-4">
                      <PaperEvidencePanel
                        externalQuery={question.trim()}
                        autoStartKey={evidenceAutoStartKey}
                      />
                    </div>
                  </div>

                  {literature.references.length > 0 && (
                    <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                        <div>
                          <h3 className="text-sm font-semibold tracking-[-0.01em] text-white">Prior-work check</h3>
                          <p className="mt-1 text-xs text-zinc-500">
                            The top 2 papers are flagged as the closest prior work — review them before committing.
                          </p>
                        </div>
                        <span className="text-xs text-zinc-500">Deselect to exclude from hypothesis generation</span>
                      </div>
                      <ul className="mt-4 space-y-2">
                        {literature.references.map((r, idx) => {
                          const key = r.url + r.title;
                          const active = !deselectedRefs.has(key);
                          const flagged = idx < 2;
                          return (
                            <li key={key}>
                              <button
                                type="button"
                                onClick={() =>
                                  setDeselectedRefs((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(key)) next.delete(key);
                                    else next.add(key);
                                    return next;
                                  })
                                }
                                className={`w-full rounded-xl border p-4 text-left transition ${
                                  flagged
                                    ? active
                                      ? "border-amber-400/60 bg-amber-500/5 hover:border-amber-300"
                                      : "border-amber-400/20 bg-transparent hover:border-amber-300/40"
                                    : active
                                      ? "border-[var(--border)] bg-[var(--input-bg)] hover:border-zinc-600"
                                      : "border-zinc-800 bg-transparent hover:border-zinc-700"
                                }`}
                              >
                                <div className="flex items-start gap-3">
                                  {flagged && (
                                    <span
                                      className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-400/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
                                      title="Flagged as closest prior work — review before running this experiment"
                                    >
                                      <Flag className="h-3 w-3" aria-hidden />
                                      Prior work #{idx + 1}
                                    </span>
                                  )}
                                  <div className="min-w-0 flex-1">
                                    <p className={`text-sm font-semibold ${active ? (flagged ? "text-amber-100" : "text-emerald-300") : "text-white"}`}>{r.title}</p>
                                    <p className={`mt-0.5 text-xs ${active ? "text-[var(--muted)]" : "text-zinc-500"}`}>
                                      {[r.authors, r.year].filter(Boolean).join(" · ")}
                                      {r.source ? ` · ${r.source}` : ""}
                                    </p>
                                    {r.snippet && (
                                      <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">{r.snippet}</p>
                                    )}
                                    {r.url && (
                                      <a
                                        href={r.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        onClick={(e) => e.stopPropagation()}
                                        className="mt-1.5 inline-flex text-xs font-medium text-emerald-300 underline-offset-4 hover:underline"
                                      >
                                        Open paper →
                                      </a>
                                    )}
                                  </div>
                                </div>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}

                  <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                    <label
                      htmlFor="lit-keywords"
                      className="mb-2 block text-sm font-semibold tracking-[-0.01em] text-white"
                    >
                      Refine literature search
                    </label>
                    <p className="mb-3 text-xs text-zinc-500">
                      Press Enter to re-run the search with these keywords appended.
                    </p>
                    <input
                      id="lit-keywords"
                      type="text"
                      value={litKeywords}
                      onChange={(e) => setLitKeywords(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key !== "Enter" || qcLoading) return;
                        e.preventDefault();
                        if (!litKeywords.trim()) return;
                        runLiteratureQc(litKeywords);
                      }}
                      placeholder="Add keywords to narrow results — e.g. assay type, model organism, target pathway, cell line…"
                      className="w-full rounded-xl border border-[var(--border)] bg-[var(--input-bg)] px-4 py-3 text-sm text-white placeholder:text-zinc-500 focus:border-emerald-500/40 focus:outline-none focus:ring-4 focus:ring-emerald-500/15"
                    />
                  </div>

                  <div>
                    <button
                      type="button"
                      onClick={runHypotheses}
                      disabled={suggestionsLoading}
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-emerald-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {suggestionsLoading && (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      )}
                      Generate top hypotheses (Agent2)
                    </button>
                  </div>
                </div>
              )}
              {error && step === "literature" && (
                <div role="alert" className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                  {error}
                </div>
              )}
            </div>

            <div ref={(el) => { slideRefs.current[2] = el; }} className="w-full min-w-full shrink-0">
              {suggestions && (
                <div className="space-y-4">
                  <div>
                    <h2 className="text-xl font-semibold tracking-[-0.02em] text-white">Top 3 hypotheses</h2>
                    <p className="mt-1 text-sm text-zinc-400">
                      Agent2 ranked these using Agent1 literature and Agent3 folding confidence.
                    </p>
                  </div>

                  {proteinModels.length > 0 && (
                    <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                      <h3 className="text-sm font-semibold tracking-[-0.01em] text-white">Agent3 protein folding confidence</h3>
                      <div className="mt-4 grid gap-4 md:grid-cols-3">
                        {proteinModels.map((model) => (
                          <article key={model.id} className="rounded-xl border border-[var(--border)] bg-[var(--input-bg)] p-3">
                            {model.structureSvgDataUri ? (
                              <img
                                src={model.structureSvgDataUri}
                                alt={`${model.name} structure`}
                                className="h-36 w-full rounded-md border border-zinc-800 object-contain bg-black"
                              />
                            ) : (
                              <div className="flex h-36 w-full items-center justify-center rounded-md border border-zinc-800 bg-black text-xs text-zinc-500">
                                Structure preview unavailable
                              </div>
                            )}
                            <h4 className="mt-3 text-sm font-semibold text-white">{model.name}</h4>
                            <p className="mt-1 text-xs text-zinc-400">UniProt {model.uniprotId} · {model.length} aa</p>
                            <p className="mt-2 text-xs font-semibold text-emerald-300">
                              Mean pLDDT: {model.meanPlddt.toFixed(1)} ({model.confidenceLabel})
                            </p>
                            <p className="mt-1 text-xs leading-relaxed text-zinc-500">{model.summary}</p>
                          </article>
                        ))}
                      </div>
                    </div>
                  )}

                  <ul className="space-y-3">
                    {suggestions.map((s) => (
                      <li key={s.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedSuggestion(s)}
                          className={`w-full rounded-2xl border p-5 text-left transition ${
                            selectedSuggestion?.id === s.id
                              ? "border-emerald-500/60 bg-emerald-500/8 ring-1 ring-emerald-500/30"
                              : "border-[var(--border)] bg-[var(--card)] hover:border-emerald-500/30 hover:bg-emerald-500/5"
                          }`}
                        >
                          <p className="font-semibold tracking-[-0.01em] text-white">{s.id}. {s.title}</p>
                          <p className="mt-1.5 text-sm leading-relaxed text-zinc-300">{s.description}</p>
                          <p className="mt-2 text-xs leading-relaxed text-zinc-500">{s.rationale}</p>
                        </button>
                      </li>
                    ))}
                  </ul>

                  <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 sm:p-6">
                    <h3 className="text-sm font-semibold tracking-[-0.01em] text-white">
                      Interactive wet-lab planner (Agent4 inputs)
                    </h3>
                    <p className="mt-1 text-xs text-zinc-500">
                      Set hard constraints. Agent4 generates a clear executable report from these.
                    </p>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <label className="text-xs text-zinc-400">
                        Budget cap (USD)
                        <input
                          type="number"
                          min={0}
                          value={plannerContext.budgetCapUsd ?? ""}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              budgetCapUsd: Number(e.target.value || 0),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                      <label className="text-xs text-zinc-400">
                        Timeline (weeks)
                        <input
                          type="number"
                          min={1}
                          value={plannerContext.timelineWeeks ?? ""}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              timelineWeeks: Number(e.target.value || 1),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                    </div>
                    <div className="mt-3 grid gap-3">
                      <label className="text-xs text-zinc-400">
                        Instruments already in lab (comma-separated)
                        <input
                          type="text"
                          value={plannerContext.availableInstruments.join(", ")}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              availableInstruments: parseList(e.target.value),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                      <label className="text-xs text-zinc-400">
                        Materials already in lab (comma-separated)
                        <input
                          type="text"
                          value={plannerContext.availableMaterials.join(", ")}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              availableMaterials: parseList(e.target.value),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                      <label className="text-xs text-zinc-400">
                        Materials needed (comma-separated)
                        <input
                          type="text"
                          value={plannerContext.requiredMaterials.join(", ")}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              requiredMaterials: parseList(e.target.value),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                      <label className="text-xs text-zinc-400">
                        Preferred assays (comma-separated; e.g. TEVC, patch-clamp, radioligand binding)
                        <input
                          type="text"
                          value={plannerContext.preferredAssays.join(", ")}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              preferredAssays: parseList(e.target.value),
                            }))
                          }
                          className="mt-1 h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 text-sm text-white"
                        />
                      </label>
                      <label className="text-xs text-zinc-400">
                        Additional constraints
                        <textarea
                          value={plannerContext.notes ?? ""}
                          onChange={(e) =>
                            setPlannerContext((prev) => ({
                              ...prev,
                              notes: e.target.value,
                            }))
                          }
                          rows={3}
                          className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--input-bg)] px-3 py-2 text-sm text-white"
                        />
                      </label>
                    </div>
                  </div>

                  <div className="pt-2">
                    <button
                      type="button"
                      onClick={runPlan}
                      disabled={!selectedSuggestion || planLoading}
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-emerald-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {planLoading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                      Generate wetlab masterplan (Agent2)
                    </button>
                  </div>
                </div>
              )}
              {error && step === "hypotheses" && (
                <div role="alert" className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                  {error}
                </div>
              )}
            </div>

            <div ref={(el) => { slideRefs.current[3] = el; }} className="w-full min-w-full shrink-0">
              {plan && (
                <section id="experiment-plan" className="scroll-mt-24 rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow)]">
                  <div className="flex flex-col gap-4 border-b border-[var(--border)] p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
                    <div>
                      <h2 className="text-xl font-semibold tracking-[-0.02em] text-white">Experiment plan</h2>
                      <p className="mt-1 max-w-3xl text-sm text-[var(--muted)]">{plan.title}</p>
                    </div>
                    <nav aria-label="Plan sections" className="flex flex-wrap gap-2">
                      {SECTION_IDS.map((id) => (
                        <button
                          key={id}
                          type="button"
                          onClick={() => {
                            setActiveSection(id);
                            document
                              .getElementById(`section-${id}`)
                              ?.scrollIntoView({ behavior: "smooth", block: "start" });
                          }}
                          className={`rounded-full px-3 py-1 text-xs font-medium capitalize transition ${
                            activeSection === id
                              ? "bg-emerald-500/20 text-emerald-100 ring-1 ring-emerald-500/40"
                              : "bg-[var(--input-bg)] text-[var(--muted)] hover:text-[var(--foreground)]"
                          }`}
                        >
                          {id}
                        </button>
                      ))}
                    </nav>
                  </div>
                  <div className="divide-y divide-[var(--border)]">
                    <PlanBlock id="section-overview" title="Overview">
                      <p className="text-sm leading-relaxed text-zinc-300">{plan.hypothesisSummary}</p>
                      {plan.protocolOriginNote && (
                        <p className="mt-3 text-sm leading-relaxed text-[var(--muted)]">
                          <span className="font-medium text-zinc-400">Protocol grounding: </span>
                          {plan.protocolOriginNote}
                        </p>
                      )}
                      {plan.executionReport && (
                        <div className="mt-4 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-5">
                          <h4 className="text-xs font-semibold uppercase tracking-wide text-emerald-200">
                            Agent4 Executable Report
                          </h4>
                          <div className="mt-3">
                            <Markdown>{plan.executionReport}</Markdown>
                          </div>
                        </div>
                      )}
                    </PlanBlock>
                    <PlanBlock id="section-protocol" title="Protocol">
                      <ol className="space-y-4">
                        {plan.protocol.map((p) => (
                          <li key={p.stepNumber} className="flex gap-4 rounded-xl border border-[var(--border)] bg-[var(--input-bg)] p-4">
                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15 text-sm font-bold text-emerald-300">{p.stepNumber}</span>
                            <div>
                              <h4 className="font-semibold text-[var(--foreground)]">{p.title}</h4>
                              <p className="mt-1 text-sm leading-relaxed text-zinc-300">{p.description}</p>
                            </div>
                          </li>
                        ))}
                      </ol>
                    </PlanBlock>
                    <PlanBlock id="section-materials" title="Materials & supply chain">
                      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
                        <table className="min-w-full text-left text-sm">
                          <thead className="bg-black/30 text-xs uppercase tracking-wide text-zinc-400">
                            <tr>
                              <th className="px-4 py-3 font-medium">Item</th>
                              <th className="px-4 py-3 font-medium">Supplier</th>
                              <th className="px-4 py-3 font-medium">Catalog</th>
                              <th className="px-4 py-3 font-medium">Qty</th>
                              <th className="px-4 py-3 font-medium text-right">Est.</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--border)]">
                            {plan.materials.map((m) => (
                              <tr key={m.name + m.supplier} className="bg-[var(--input-bg)]">
                                <td className="px-4 py-3 font-medium text-zinc-200">{m.name}</td>
                                <td className="px-4 py-3 text-zinc-400">{m.supplier}</td>
                                <td className="px-4 py-3 text-zinc-400">{m.catalogNumber ?? "-"}</td>
                                <td className="px-4 py-3 text-zinc-400">{m.quantity}</td>
                                <td className="px-4 py-3 text-right tabular-nums text-zinc-300">
                                  {m.lineTotal != null
                                    ? fmtMoney(m.lineTotal, m.currency ?? plan.budget.currency)
                                    : "-"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </PlanBlock>
                    <PlanBlock id="section-budget" title="Budget">
                      <ul className="space-y-2">
                        {plan.budget.lines.map((line) => (
                          <li key={line.description} className="flex items-center justify-between gap-3 rounded-xl border border-[var(--border)] bg-[var(--input-bg)] px-4 py-3 text-sm">
                            <div>
                              <p className="font-medium text-zinc-200">{line.category}</p>
                              <p className="text-xs text-[var(--muted)]">{line.description}</p>
                            </div>
                            <span className="shrink-0 font-semibold tabular-nums text-emerald-300">
                              {fmtMoney(line.amount, line.currency)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </PlanBlock>
                    <PlanBlock id="section-timeline" title="Timeline">
                      <ul className="space-y-3">
                        {plan.timeline.map((t) => (
                          <li key={t.phase} className="rounded-xl border border-[var(--border)] bg-[var(--input-bg)] p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <h4 className="font-semibold text-[var(--foreground)]">{t.phase}</h4>
                              <span className="rounded-md bg-black/35 px-2 py-0.5 text-xs tabular-nums text-zinc-300">
                                wk {t.startWeek}-{t.endWeek}
                              </span>
                            </div>
                            <p className="mt-1 text-sm text-zinc-400">{t.description}</p>
                          </li>
                        ))}
                      </ul>
                    </PlanBlock>
                    <PlanBlock id="section-validation" title="Validation">
                      <div className="grid gap-4 md:grid-cols-2">
                        <ValidationList title="Primary endpoints" items={plan.validation.primaryEndpoints} />
                        <ValidationList title="Success criteria" items={plan.validation.successCriteria} />
                        <ValidationList title="Controls" items={plan.validation.controls} />
                        <ValidationList title="Analytical methods" items={plan.validation.analyticalMethods} />
                      </div>
                    </PlanBlock>
                  </div>
                </section>
              )}
            </div>
          </div>
        </div>
      </main>

      <footer className="relative mt-16 border-t border-[var(--border)] py-8 text-center text-xs text-[var(--muted)]">
        Agent1 streams literature evidence, Agent2 ranks hypotheses, Agent3 supplies folding confidence when needed.
      </footer>
    </div>
  );
}

function StepTracker({
  step,
  stepIndex,
  canOpenValidation,
  canOpenHypotheses,
  canOpenPlan,
  onOpenHypothesis,
  onOpenValidation,
  onOpenHypotheses,
  onOpenPlan,
}: {
  step: Step;
  stepIndex: number;
  canOpenValidation: boolean;
  canOpenHypotheses: boolean;
  canOpenPlan: boolean;
  onOpenHypothesis: () => void;
  onOpenValidation: () => void;
  onOpenHypotheses: () => void;
  onOpenPlan: () => void;
}) {
  const items = [
    { n: "01", title: "Question", active: step === "input", locked: false, onClick: onOpenHypothesis },
    { n: "02", title: "Literature Check", active: step === "literature", locked: !canOpenValidation, onClick: onOpenValidation },
    { n: "03", title: "Hypotheses", active: step === "hypotheses", locked: !canOpenHypotheses, onClick: onOpenHypotheses },
    { n: "04", title: "Experiment", active: step === "plan", locked: !canOpenPlan, onClick: onOpenPlan },
  ] as const;

  return (
    <ol className="relative mb-10 grid w-full grid-cols-2 gap-7 sm:grid-cols-4 sm:gap-4">
      <div aria-hidden className="pointer-events-none absolute left-0 right-0 top-[22px] hidden h-px bg-white/15 sm:block">
        <div
          className="h-full bg-white/60 transition-[width] duration-500 ease-in-out"
          style={{ width: stepIndex === 3 ? "100%" : `calc(${(stepIndex + 1) * 25}% - 10px)` }}
        />
      </div>
      {items.map((item) => (
        <li key={item.n} className="relative bg-transparent">
          <button
            type="button"
            disabled={item.locked}
            onClick={item.onClick}
            className={`text-left font-medium tracking-[-0.01em] transition ${item.locked ? "cursor-not-allowed" : "cursor-pointer"}`}
          >
            <span className={`block text-[11px] tracking-[0.12em] ${item.active ? "text-white" : "text-zinc-500"}`}>{item.n}</span>
            <span className={`mt-4 block text-sm ${item.active ? "text-white" : item.locked ? "text-zinc-600" : "text-zinc-400 hover:text-zinc-200"}`}>{item.title}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function PlanBlock({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-24 p-5 sm:p-6">
      <h3 className="mb-4 text-lg font-semibold capitalize tracking-[-0.02em] text-white">{title}</h3>
      {children}
    </div>
  );
}

function ValidationList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--input-bg)] p-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</h4>
      <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-zinc-300">
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </div>
  );
}
