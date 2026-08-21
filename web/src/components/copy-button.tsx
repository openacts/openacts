"use client";

import { useState } from "react";

function CopyIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function CopyButton({
  value,
  label,
}: {
  value: string;
  label: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 4000);
  }

  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex min-h-11 items-center gap-2 rounded-sm text-sm font-semibold text-action hover:text-action-strong focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
    >
      <CopyIcon />
      <span>{label}</span>
      <span role="status" className="id text-muted">
        {state === "copied" ? "copied" : state === "failed" ? "copy failed" : ""}
      </span>
    </button>
  );
}
