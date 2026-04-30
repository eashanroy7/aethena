"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  h1: (props) => (
    <h1
      {...props}
      className="mt-2 mb-3 text-lg font-semibold tracking-[-0.02em] text-white"
    />
  ),
  h2: (props) => (
    <h2
      {...props}
      className="mt-6 mb-2 text-base font-semibold tracking-[-0.01em] text-white"
    />
  ),
  h3: (props) => (
    <h3
      {...props}
      className="mt-5 mb-2 text-sm font-semibold tracking-[-0.01em] text-emerald-200"
    />
  ),
  h4: (props) => (
    <h4
      {...props}
      className="mt-4 mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-300"
    />
  ),
  p: (props) => (
    <p
      {...props}
      className="my-2 text-sm leading-relaxed text-zinc-200"
    />
  ),
  ul: (props) => (
    <ul
      {...props}
      className="my-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-zinc-200 marker:text-zinc-500"
    />
  ),
  ol: (props) => (
    <ol
      {...props}
      className="my-2 list-decimal space-y-1 pl-5 text-sm leading-relaxed text-zinc-200 marker:text-zinc-500"
    />
  ),
  li: (props) => <li {...props} className="pl-1" />,
  strong: (props) => (
    <strong {...props} className="font-semibold text-white" />
  ),
  em: (props) => <em {...props} className="text-zinc-300" />,
  blockquote: (props) => (
    <blockquote
      {...props}
      className="my-3 border-l-2 border-emerald-500/40 bg-emerald-500/5 px-3 py-1 text-xs italic leading-relaxed text-emerald-100"
    />
  ),
  hr: () => <hr className="my-4 border-zinc-800" />,
  a: (props) => (
    <a
      {...props}
      target="_blank"
      rel="noreferrer"
      className="text-emerald-300 underline-offset-4 hover:underline"
    />
  ),
  code: (props: ComponentPropsWithoutRef<"code"> & { inline?: boolean }) => {
    const { inline, className, children, ...rest } = props;
    if (inline) {
      return (
        <code
          {...rest}
          className="rounded bg-zinc-800/80 px-1 py-0.5 font-mono text-[12px] text-emerald-200"
        >
          {children}
        </code>
      );
    }
    return (
      <code
        {...rest}
        className={`block whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-900/70 p-3 font-mono text-[12px] text-zinc-100 ${className ?? ""}`}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }: { children?: ReactNode }) => (
    <pre className="my-3 overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-900/70 p-3 font-mono text-[12px] leading-relaxed text-zinc-100">
      {children}
    </pre>
  ),
  table: (props) => (
    <div className="my-3 overflow-x-auto rounded-lg border border-zinc-800">
      <table
        {...props}
        className="min-w-full border-collapse text-left text-xs text-zinc-200"
      />
    </div>
  ),
  thead: (props) => <thead {...props} className="bg-black/40" />,
  tbody: (props) => (
    <tbody {...props} className="divide-y divide-zinc-800" />
  ),
  tr: (props) => <tr {...props} className="bg-zinc-900/40" />,
  th: (props) => (
    <th
      {...props}
      className="border-b border-zinc-800 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400"
    />
  ),
  td: (props) => (
    <td
      {...props}
      className="px-3 py-2 align-top text-xs text-zinc-200"
    />
  ),
  input: (props) => {
    if (props.type === "checkbox") {
      return (
        <input
          {...props}
          disabled
          className="mr-2 h-3.5 w-3.5 translate-y-[2px] rounded border-zinc-600 bg-zinc-800 accent-emerald-400"
        />
      );
    }
    return <input {...props} />;
  },
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
