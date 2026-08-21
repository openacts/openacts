import Link from "next/link";

import { textKindLabel } from "@/lib/act";
import type { ActSummary, SourceDocument, SourceSpan } from "@/lib/contracts";
import { sourceHref } from "@/lib/provision";

export function CurrentnessLine({ act }: { act: ActSummary }) {
  return (
    <p className="id mt-3 text-muted">
      {textKindLabel(act.text_kind).toLowerCase()}
      <span aria-hidden="true"> &middot; </span>
      status {act.status}
      <span aria-hidden="true"> &middot; </span>
      {act.checked_through_date
        ? `checked through ${act.checked_through_date}`
        : "currentness not established"}
    </p>
  );
}

export function SourceFootnote({
  sources,
  spans,
  fidelity,
  release,
}: {
  sources: readonly SourceDocument[];
  spans: readonly SourceSpan[];
  fidelity: string;
  release: string | null;
}) {
  const primary = sources[0];
  const page = spans[0]?.pdf_page;

  return (
    <div className="mt-10 border-t border-line pt-4">
      <p className="id text-muted">
        {primary ? (
          <>
            <Link
              href={sourceHref(primary.source_id)}
              className="rounded-sm text-ink underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
            >
              {primary.document_title ?? "Untitled source"}
            </Link>
            {page ? `, page ${page} of ${primary.page_count}` : ""}
            <span aria-hidden="true"> &middot; </span>
          </>
        ) : null}
        text fidelity {fidelity}
        <span aria-hidden="true"> &middot; </span>
        {release ?? "unidentified release"}
      </p>
    </div>
  );
}
