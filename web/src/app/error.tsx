"use client";

import Link from "next/link";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main id="main-content">
      <section className="mx-auto max-w-3xl px-5 py-16 sm:px-8">
        <h1 className="font-reading text-4xl text-ink">
          This page could not be loaded
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-muted">
          The corpus could not be read just now. No legal text is shown because
          showing partial or stale wording would be worse than showing none.
        </p>
        {error.digest ? (
          <p className="mt-4 font-mono text-xs text-muted wrap-anywhere">
            Reference {error.digest}
          </p>
        ) : null}
        <div className="mt-8 flex flex-wrap gap-4">
          <button
            type="button"
            onClick={reset}
            className="rounded-sm border border-action px-4 py-2 text-sm font-semibold text-action focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            Try again
          </button>
          <Link
            href="/acts"
            className="rounded-sm px-4 py-2 text-sm font-semibold text-muted underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            Back to all Acts
          </Link>
        </div>
      </section>
    </main>
  );
}
