import Link from "next/link";
import { connection } from "next/server";

import { textKindLabel } from "@/lib/act";
import { fetchActs } from "@/lib/api";
import {
  ACTS_PAGE_SIZE,
  nextOffset,
  parseOffset,
  previousOffset,
} from "@/lib/pagination";
import { actHref } from "@/lib/provision";

export const metadata = {
  title: "Act index",
  description:
    "Every legal instrument in the active OpenActs corpus release.",
};

function pageHref(offset: number): string {
  return offset === 0 ? "/acts" : `/acts?offset=${offset}`;
}

export default async function ActsPage({ searchParams }: PageProps<"/acts">) {
  await connection();

  const offset = parseOffset((await searchParams).offset);

  // Deliberately uncaught: catching here would serve an outage as HTTP 200.
  const response = await fetchActs(offset, ACTS_PAGE_SIZE);
  const { items, pagination } = response.data;

  const previous = previousOffset(pagination.offset, pagination.limit);
  const next = nextOffset(pagination.offset, pagination.limit, pagination.total);
  const lastShown = pagination.offset + items.length;

  return (
    <main id="main-content">
      <section className="mx-auto max-w-4xl px-5 py-12 sm:px-8 sm:py-16">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div>
            <h1 className="font-reading text-[clamp(2rem,1.5rem+2vw,2.75rem)] font-medium leading-tight text-ink">
              Act index
            </h1>
            <p className="mt-3 max-w-xl text-[0.9375rem] leading-7 text-muted">
              Every legal instrument in the active corpus release.
            </p>
          </div>
          <p className="id text-muted">
            {items.length === 0
              ? `0 of ${pagination.total}`
              : `Showing ${pagination.offset + 1}–${lastShown} of ${pagination.total}`}
          </p>
        </div>

        {items.length === 0 ? (
          <div className="mt-10 border-t border-line pt-6 text-[0.9375rem] leading-7 text-muted">
            <p>
              {pagination.offset > 0
                ? "This page is past the end of the index."
                : "No Acts are published in this release yet."}
            </p>
            {pagination.offset > 0 ? (
              <Link
                href="/acts"
                className="mt-3 inline-block rounded-sm font-semibold text-action underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
              >
                Back to the first page
              </Link>
            ) : null}
          </div>
        ) : (
          <ul className="mt-10 border-t border-line">
            {items.map((act) => (
              <li
                key={act.act_id}
                className="flex gap-6 border-b border-line py-7 sm:gap-8"
              >
                <span className="id w-14 flex-none pt-1.5 text-sm text-muted">
                  {act.year}
                </span>
                <div className="min-w-0 flex-1">
                  <Link
                    href={actHref(act.act_id)}
                    className="rounded-sm font-reading text-2xl font-medium leading-snug text-ink hover:text-action-strong hover:underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
                  >
                    {act.official_title}
                  </Link>
                  <p className="mt-1.5 text-[0.9375rem] text-muted">
                    {act.citation ?? act.short_title ?? "No citation recorded"}
                  </p>
                  {/* Three independent claims, three separate statements. */}
                  <p className="id mt-2.5 text-muted">
                    {textKindLabel(act.text_kind).toLowerCase()} &middot; status{" "}
                    {act.status} &middot;{" "}
                    {act.checked_through_date
                      ? `checked through ${act.checked_through_date}`
                      : "currentness not established"}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}

        {previous !== null || next !== null ? (
          <nav
            aria-label="Act index pages"
            className="mt-8 flex items-center justify-between gap-6"
          >
            {previous === null ? (
              <span />
            ) : (
              <Link
                href={pageHref(previous)}
                rel="prev"
                className="inline-flex min-h-11 items-center rounded-sm text-sm font-semibold text-action focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
              >
                &larr; Previous {pagination.limit}
              </Link>
            )}
            {next === null ? (
              <span />
            ) : (
              <Link
                href={pageHref(next)}
                rel="next"
                className="inline-flex min-h-11 items-center rounded-sm text-sm font-semibold text-action focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
              >
                Next {pagination.limit} &rarr;
              </Link>
            )}
          </nav>
        ) : null}
      </section>
    </main>
  );
}
