"use client";

import Link from "next/link";
import { useId, useRef, useState } from "react";

import type { SearchItem } from "@/lib/contracts";
import { provisionHref, provisionTitle } from "@/lib/provision";
import {
  excerptBeyondTitle,
  matchReason,
  runSearch,
  splitResults,
  type SearchOutcome,
} from "@/lib/search";

type State =
  | { name: "idle" }
  | { name: "submitting" }
  | { name: "invalid"; message: string }
  | { name: "unavailable"; message: string }
  | { name: "results"; items: SearchItem[] };

function SearchIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function Result({ item }: { item: SearchItem }) {
  const href = item.provision
    ? provisionHref(item.provision.provision_id)
    : `/acts/${encodeURIComponent(item.act.act_id)}`;
  const label = item.provision?.display_label;
  const heading = item.provision
    ? provisionTitle(item.provision)
    : item.act.official_title;
  const excerpt = excerptBeyondTitle(item.excerpt, heading);

  return (
    <li className="border-b border-line py-5">
      {item.provision ? (
        <p className="id text-muted">
          {item.act.official_title}
          {item.breadcrumb.map((step) => (
            <span key={step.provision_id}>
              {" "}
              <span aria-hidden="true">&rsaquo;</span> {provisionTitle(step)}
            </span>
          ))}
        </p>
      ) : null}
      <Link
        href={href}
        className="mt-1 inline-block rounded-sm font-reading text-[1.1875rem] text-ink hover:text-action-strong hover:underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
      >
        {label && !heading.startsWith(label) ? (
          <span className="text-action-strong">{label} </span>
        ) : null}
        {heading}
      </Link>
      {excerpt ? (
        <p className="mt-1.5 max-w-[66ch] font-reading text-[1rem] leading-7 text-muted">
          {excerpt}
        </p>
      ) : null}
      <p className="id mt-1.5 text-muted">{matchReason(item)}</p>
    </li>
  );
}

export function Search() {
  const [state, setState] = useState<State>({ name: "idle" });
  const [query, setQuery] = useState("");
  const inFlight = useRef<AbortController | null>(null);
  const fieldId = useId();

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setState({ name: "submitting" });

    let outcome: SearchOutcome;
    try {
      outcome = await runSearch(query, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      outcome = {
        status: "unavailable",
        message:
          error instanceof Error
            ? "The corpus could not be searched just now."
            : "The corpus could not be searched just now.",
      };
    }

    if (controller.signal.aborted) {
      return;
    }

    if (outcome.status === "results") {
      setState({ name: "results", items: outcome.items });
    } else if (outcome.status === "invalid") {
      setState({ name: "invalid", message: outcome.message });
    } else {
      setState({ name: "unavailable", message: outcome.message });
    }
  }

  const split = state.name === "results" ? splitResults(state.items) : null;
  const announcement =
    state.name === "submitting"
      ? "Searching"
      : state.name === "results"
        ? state.items.length === 0
          ? "No provisions matched"
          : `${state.items.length} result${state.items.length === 1 ? "" : "s"}`
        : "";

  return (
    <div>
      <form onSubmit={submit} role="search">
        <label htmlFor={fieldId} className="sr-only">
          Search Nigerian legislation
        </label>
        <div className="flex max-w-[38rem] items-center gap-3.5 border-b-2 border-action px-1 py-3">
          <span className="flex flex-none text-action">
            <SearchIcon />
          </span>
          <input
            id={fieldId}
            type="search"
            name="q"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoComplete="off"
            placeholder="Act, citation, section, or wording"
            aria-describedby={`${fieldId}-privacy`}
            aria-invalid={state.name === "invalid" ? true : undefined}
            className="min-h-11 w-full bg-transparent text-[1.25rem] text-ink outline-none placeholder:text-muted"
          />
          <button
            type="submit"
            className="min-h-11 flex-none rounded-sm px-2 text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            Search
          </button>
        </div>
      </form>

      <p id={`${fieldId}-privacy`} className="id mt-3 max-w-[38rem] text-muted">
        Your search is sent only to the corpus API. It is never written to a
        URL, a log, or storage.
      </p>

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {state.name === "invalid" ? (
        <p className="mt-5 max-w-[38rem] text-[0.9375rem] leading-7 text-muted">
          {state.message}
        </p>
      ) : null}

      {state.name === "unavailable" ? (
        <div className="mt-5 max-w-[38rem]">
          <p className="text-[0.9375rem] font-semibold text-ink">
            {state.message}
          </p>
          <p className="mt-1 text-[0.9375rem] leading-7 text-muted">
            This is usually temporary. Searching again re-reads the active
            corpus release.
          </p>
        </div>
      ) : null}

      {state.name === "results" && state.items.length === 0 ? (
        <div className="mt-8 max-w-[38rem] border-t border-line pt-5">
          <p className="text-[0.9375rem] leading-7 text-ink">
            No provision in this release matches that search.
          </p>
          <p className="mt-1 text-[0.9375rem] leading-7 text-muted">
            Search covers the wording, headings and titles of the Acts published
            in this corpus release. It does not reach Acts that are not
            published yet.
          </p>
        </div>
      ) : null}

      {split && split.exact.length > 0 ? (
        <section className="mt-10">
          <h2 className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-action">
            Exact match
          </h2>
          <ul className="mt-2 border-t border-line">
            {split.exact.map((item) => (
              <Result
                key={`${item.kind}-${item.provision?.provision_id ?? item.act.act_id}`}
                item={item}
              />
            ))}
          </ul>
        </section>
      ) : null}

      {split && split.lexical.length > 0 ? (
        <section className="mt-10">
          <h2 className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
            Also containing your words
          </h2>
          <ul className="mt-2 border-t border-line">
            {split.lexical.map((item) => (
              <Result
                key={`${item.kind}-${item.provision?.provision_id ?? item.act.act_id}`}
                item={item}
              />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
