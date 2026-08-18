import Link from "next/link";

import { textKindLabel } from "@/lib/act";
import { fetchActs } from "@/lib/api";

// Dates stay in the API's ISO form: server-locale formatting would render
// differently per deployment, and currentness boundaries must be unambiguous.
function formatDate(value: string | null): string {
  return value ?? "date not established";
}

function StatusBadges({
  textKind,
  status,
  checkedThroughDate,
}: {
  textKind: string;
  status: string;
  checkedThroughDate: string | null;
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-2 text-xs">
      <span className="rounded-full border border-line px-3 py-1 font-medium uppercase tracking-[0.13em] text-muted">
        {textKindLabel(textKind)}
      </span>
      <span className="rounded-full border border-line px-3 py-1 font-semibold uppercase tracking-[0.13em] text-muted">
        {status}
      </span>
      <span className="rounded-full border border-line px-3 py-1 font-medium text-muted">
        checked through {formatDate(checkedThroughDate)}
      </span>
    </div>
  );
}

export default async function ActsPage() {
  // Deliberately uncaught. A failure here means the corpus is unreadable, and
  // rendering an apology with a 200 would tell crawlers and uptime checks the
  // page is healthy. Letting it throw yields a 500 and error.tsx renders the UI.
  const response = await fetchActs(0, 50);
  const { items, pagination } = response.data;

  return (
    <main id="main-content">
      <section className="mx-auto max-w-7xl px-5 py-16 sm:px-8">
        <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-reading text-4xl text-ink">Act index</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
              Browse legal instruments in the active corpus release.
            </p>
          </div>
          <p className="text-sm text-muted">
            Showing {Math.min(pagination.limit, pagination.total)} of{" "}
            {pagination.total}
          </p>
        </div>

        {items.length === 0 ? (
          <p className="rounded-lg border border-line bg-surface px-6 py-5 text-sm text-muted">
            No acts are available in this release yet.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {items.map((act) => (
              <article
                key={act.act_id}
                className="border border-line bg-surface px-6 py-5 transition duration-200 hover:border-action/80"
              >
                <Link
                  href={`/acts/${encodeURIComponent(act.act_id)}`}
                  className="font-reading text-xl leading-snug text-ink hover:underline"
                >
                  {act.official_title}
                </Link>
                <p className="mt-1 text-sm text-muted">
                  {act.short_title ?? act.official_title}
                </p>
                <p className="mt-2 text-xs text-muted">
                  {act.number ?? "No assigned number"} · {act.year} ·{" "}
                  {act.citation ?? "No citation"}
                </p>
                <StatusBadges
                  textKind={act.text_kind}
                  status={act.status}
                  checkedThroughDate={act.checked_through_date}
                />
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

