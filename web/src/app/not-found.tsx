import Link from "next/link";

export const metadata = {
  title: "Not in this corpus release",
};

export default function NotFound() {
  return (
    <main id="main-content">
      <section className="mx-auto max-w-3xl px-5 py-16 sm:px-8 sm:py-24">
        <p className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
          Not found
        </p>
        <h1 className="mt-1 max-w-[20ch] text-balance font-reading text-[clamp(2rem,1.5rem+2vw,2.75rem)] font-medium leading-[1.15] text-ink">
          Not in this corpus release
        </h1>
        <p className="mt-6 max-w-[46ch] text-[0.9375rem] leading-7 text-muted">
          No Act, Provision, or Source in the active release carries that
          identifier. It may have been mistyped, or it may not be published yet.
        </p>
        <p className="mt-3 max-w-[46ch] text-[0.9375rem] leading-7 text-muted">
          Identifiers are exact, so a near miss is not a near match. Nothing is
          guessed on your behalf.
        </p>
        <div className="mt-8 flex flex-wrap gap-x-8 gap-y-3">
          <Link
            href="/acts"
            className="inline-flex min-h-11 items-center rounded-sm text-sm font-semibold text-action hover:text-action-strong focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            Browse all Acts
          </Link>
          <Link
            href="/"
            className="inline-flex min-h-11 items-center rounded-sm text-sm font-semibold text-muted hover:text-ink focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            Search from the home page
          </Link>
        </div>
      </section>
    </main>
  );
}
