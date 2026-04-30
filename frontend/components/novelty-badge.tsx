import type { NoveltySignal } from "@/lib/types";

const copy: Record<
  NoveltySignal,
  { label: string; description: string; className: string }
> = {
  not_found: {
    label: "Not found",
    description: "No close protocol match in quick scan",
    className:
      "bg-[var(--badge-nf-bg)] text-[var(--badge-nf-fg)] ring-[var(--badge-nf-ring)]",
  },
  similar_work_exists: {
    label: "Similar work exists",
    description: "Related protocols or studies surfaced",
    className:
      "bg-[var(--badge-sw-bg)] text-[var(--badge-sw-fg)] ring-[var(--badge-sw-ring)]",
  },
  exact_match_found: {
    label: "Exact match found",
    description: "Very close or standard protocol family",
    className:
      "bg-[var(--badge-em-bg)] text-[var(--badge-em-fg)] ring-[var(--badge-em-ring)]",
  },
};

export function NoveltyBadge({ signal }: { signal: NoveltySignal }) {
  const cfg = copy[signal];
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset ${cfg.className}`}
    >
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-40 motion-reduce:animate-none" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-current" />
      </span>
      {cfg.label}
    </span>
  );
}

export function noveltyHelp(signal: NoveltySignal): string {
  return copy[signal].description;
}
