import Link from "next/link";
import { notFound } from "next/navigation";

import { currentnessText } from "@/lib/act";
import { OpenActsApiError, fetchActContents, fetchActDetail } from "@/lib/api";
import type {
  ActContentsData,
  ActDateKind,
  ActDetail,
  ApiResponse,
} from "@/lib/contracts";

const DATE_LABELS: Record<ActDateKind, string> = {
  assent: "Assent",
  publication: "Publication",
  commencement: "Commencement",
  repeal: "Repeal",
};

export default async function ActDetailPage({
  params,
}: {
  params: Promise<{ actId: string }>;
}) {
  const { actId } = await params;

  let detail: ApiResponse<ActDetail>;
  try {
    detail = await fetchActDetail(actId);
  } catch (error) {
    // A 4xx means this Act is not addressable, which is a real 404. Anything
    // else is our failure, so it propagates and the response carries a 500
    // rather than a 200 that would let crawlers index an outage as content.
    if (error instanceof OpenActsApiError && error.status >= 400 && error.status < 500) {
      notFound();
    }
    throw error;
  }

  // Contents are secondary: losing them should not take down Act metadata we
  // already hold, so this one failure degrades to an explanatory panel.
  let contents: ApiResponse<ActContentsData> | null = null;
  try {
    contents = await fetchActContents(actId);
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
  }

  const { act, sources } = detail.data;
  const outline = contents?.data.items ?? [];

  return (
    <main id="main-content">
      <section className="mx-auto max-w-5xl px-5 py-16 sm:px-8">
        <Link href="/acts" className="text-sm text-muted hover:underline">
          ← Back to all Acts
        </Link>

        <article className="mt-5 border-b border-line pb-8">
          <h1 className="font-reading text-4xl leading-tight text-ink">
            {act.titles.official}
          </h1>
          {act.titles.short && act.titles.short !== act.titles.official ? (
            <p className="mt-2 text-sm text-muted">{act.titles.short}</p>
          ) : null}
          <p className="mt-2 text-sm text-muted">
            {act.citation ?? "No citation recorded"} · {act.year}
            {act.number ? ` · No. ${act.number}` : ""}
          </p>
          <p className="mt-3 text-xs text-muted">{currentnessText(act)}</p>
          {act.titles.long ? (
            <p className="mt-6 max-w-2xl font-reading text-lg leading-8 text-ink">
              {act.titles.long}
            </p>
          ) : null}
        </article>

        <section className="mt-10 grid gap-10 lg:grid-cols-[2fr_1fr]">
          <div>
            <h2 className="font-reading text-2xl text-ink">Contents</h2>
            {outline.length > 0 ? (
              <ol className="mt-4 space-y-2">
                {outline.map((item) => (
                  <li
                    key={item.provision_id}
                    className="rounded-md border border-line bg-surface px-4 py-3"
                    style={{ marginLeft: `${item.depth * 1.25}rem` }}
                  >
                    <p className="font-medium text-ink">
                      {item.display_label ?? item.node_type}
                      {item.heading ? ` · ${item.heading}` : ""}
                    </p>
                    <p className="text-sm text-muted">
                      {item.has_content ? "Has provision text" : "Structure only"}
                    </p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-4 rounded-md border border-line bg-surface px-4 py-4 text-sm text-muted">
                {contents
                  ? "No outline nodes are recorded for this Act yet."
                  : "Contents are unavailable right now. The Act metadata above is unaffected."}
              </p>
            )}
          </div>

          <aside className="space-y-8">
            <div className="border border-line bg-surface px-6 py-5">
              <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
                Sources ({sources.length})
              </h2>
              <ul className="mt-3 space-y-3 text-sm">
                {sources.map((source) => (
                  <li key={source.source_id} className="text-ink">
                    {source.document_title ?? "Untitled source"}
                    {source.document_publisher ? (
                      <span className="block text-xs text-muted">
                        {source.document_publisher}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>

            <div className="border border-line bg-surface px-6 py-5">
              <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
                Dates
              </h2>
              <dl className="mt-3 space-y-2 text-sm">
                {(Object.keys(DATE_LABELS) as ActDateKind[]).map((kind) => (
                  <div key={kind} className="flex justify-between gap-4">
                    <dt className="text-muted">{DATE_LABELS[kind]}</dt>
                    <dd className="text-ink">
                      {act.dates[kind]?.date ?? "Not recorded"}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </aside>
        </section>
      </section>
    </main>
  );
}
