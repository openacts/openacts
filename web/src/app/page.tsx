import Link from "next/link";

import { Search } from "@/components/search";
import { textKindLabel } from "@/lib/act";
import { OpenActsApiError, fetchActs } from "@/lib/api";
import type { ActSummary } from "@/lib/contracts";
import { ACTS_PAGE_SIZE } from "@/lib/pagination";
import { actHref } from "@/lib/provision";

// The Act list is context beside the search field, not the reason the page
// exists. If it cannot be read the field still works, so this degrades rather
// than taking the home page down with it.
async function loadActs(): Promise<ActSummary[] | null> {
  try {
    const response = await fetchActs(0, ACTS_PAGE_SIZE);
    return response.data.items;
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
    return null;
  }
}

export default async function Home() {
  const acts = await loadActs();

  return (
    <main id="main-content">
      <section className="mx-auto max-w-4xl px-5 py-16 sm:px-8 sm:py-24">
        <h1 className="max-w-[18ch] text-balance font-reading text-[clamp(2.5rem,2rem+3vw,3.75rem)] font-medium leading-[1.05] tracking-[-0.02em] text-ink">
          Read the law at the provision.
        </h1>
        <p className="mt-6 max-w-[34rem] text-[1.0625rem] leading-8 text-muted">
          Nigerian Acts as exact, navigable legal text — every provision
          permanently linked, every word traceable to the document it came from.
        </p>

        <div className="mt-12">
          <Search />
        </div>

        <h2 className="mt-20 text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
          In this release
        </h2>
        {acts === null ? (
          <p className="mt-4 text-[0.9375rem] leading-7 text-muted">
            The Act list could not be read just now. Search above still works.
          </p>
        ) : (
          <ul className="mt-4 border-t border-line">
            {acts.map((act) => (
              <li
                key={act.act_id}
                className="flex gap-6 border-b border-line py-6 sm:gap-8"
              >
                <span className="id w-14 flex-none pt-1.5 text-sm text-muted">
                  {act.year}
                </span>
                <div className="min-w-0 flex-1">
                  <Link
                    href={actHref(act.act_id)}
                    className="rounded-sm font-reading text-[1.375rem] leading-snug text-ink hover:text-action-strong hover:underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
                  >
                    {act.official_title}
                  </Link>
                  <p className="id mt-2 text-muted">
                    {textKindLabel(act.text_kind).toLowerCase()}
                    <span aria-hidden="true"> &middot; </span>
                    {act.citation ?? "no citation recorded"}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
        <Link
          href="/acts"
          className="mt-6 inline-block rounded-sm text-[0.9375rem] font-semibold text-action focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
        >
          Browse the full Act index &rarr;
        </Link>
      </section>
    </main>
  );
}
