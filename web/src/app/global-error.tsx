"use client";

import "./globals.css";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-paper text-ink antialiased">
        <main id="main-content">
          <section className="mx-auto max-w-3xl px-5 py-16 sm:px-8">
            <h1 className="text-[2rem] font-medium leading-tight text-ink">
              OpenActs could not start
            </h1>
            <p className="mt-4 max-w-[46ch] text-[0.9375rem] leading-7 text-muted">
              The application failed before any legal text could be read. No
              wording is shown, because partial or stale wording would be worse
              than none.
            </p>
            {error.digest ? (
              <p className="mt-4 font-mono text-xs text-muted wrap-anywhere">
                Reference {error.digest}
              </p>
            ) : null}
            <button
              type="button"
              onClick={reset}
              className="mt-8 min-h-11 rounded-sm border border-action px-4 text-sm font-semibold text-action focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
            >
              Try again
            </button>
          </section>
        </main>
      </body>
    </html>
  );
}
