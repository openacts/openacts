import Link from "next/link";

import { OpenActsApiError, fetchActContents } from "@/lib/api";
import type { ActContentsData, ApiResponse } from "@/lib/contracts";
import {
  ACT_OUTLINE_MAX_DEPTH,
  outlineNodeCount,
  topLevelOutline,
} from "@/lib/outline";
import { nodeTypeLabel, provisionHref } from "@/lib/provision";

const PLACEHOLDERS = [0, 1, 2, 3, 4, 5];

export function ActArrangementSkeleton() {
  return (
    <div role="status" aria-live="polite" className="mt-6 flex flex-col gap-3">
      <span className="sr-only">Loading the arrangement of this Act…</span>
      {PLACEHOLDERS.map((placeholder) => (
        <span
          key={placeholder}
          aria-hidden="true"
          className="block h-12 animate-pulse rounded-sm bg-line motion-reduce:animate-none"
        />
      ))}
    </div>
  );
}

type Load =
  | { ok: true; response: ApiResponse<ActContentsData> }
  | { ok: false; requestId: string | null };

async function loadContents(actId: string): Promise<Load> {
  try {
    return { ok: true, response: await fetchActContents(actId, undefined, ACT_OUTLINE_MAX_DEPTH) };
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
    return { ok: false, requestId: error.requestId };
  }
}

export async function ActArrangement({
  actId,
  actRelease,
}: {
  actId: string;
  actRelease: string | null;
}) {
  const load = await loadContents(actId);

  if (!load.ok) {
    return (
      <div role="status" className="mt-6 border-t border-line pt-5">
        <p className="text-[0.9375rem] font-semibold text-ink">
          The arrangement is unavailable right now
        </p>
        <p className="mt-1 text-[0.9375rem] leading-7 text-muted">
          The Act details above are unaffected. Reloading re-reads the contents
          from the API.
        </p>
        {load.requestId ? (
          <p className="id mt-2 text-muted wrap-anywhere">
            Reference {load.requestId}
          </p>
        ) : null}
      </div>
    );
  }

  const { items } = load.response.data;
  const sections = topLevelOutline(items);

  if (sections.length === 0) {
    return (
      <p className="mt-6 border-t border-line pt-5 text-[0.9375rem] text-muted">
        No structure is recorded for this Act yet.
      </p>
    );
  }

  return (
    <>
      {load.response.meta.corpus_release !== actRelease ? (
        <p role="status" className="mt-4 text-[0.9375rem] leading-7 text-muted">
          The corpus release changed while this page was being assembled. Reload
          to read a single release.
        </p>
      ) : null}
      <p className="id mt-2 text-muted">
        Top-level structure &middot; {outlineNodeCount(sections)} divisions
      </p>
      <ol className="mt-5 border-t border-line">
        {sections.map((section) => (
          <li key={section.node.provision_id} className="border-b border-line py-4">
            <Link
              href={provisionHref(section.node.provision_id)}
              className="flex min-h-11 flex-wrap items-baseline gap-x-3 gap-y-1 rounded-sm focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
            >
              {section.node.display_label ? (
                <span className="font-reading text-[1.1875rem] font-medium text-action-strong">
                  {section.node.display_label}
                </span>
              ) : null}
              <span className="font-reading text-[1.1875rem] text-ink">
                {section.node.heading ?? nodeTypeLabel(section.node.node_type)}
              </span>
            </Link>
            {section.children.length > 0 ? (
              <ol className="mt-1 ml-4 border-l border-line sm:ml-8">
                {section.children.map((child) => (
                  <li key={child.provision_id}>
                    <Link
                      href={provisionHref(child.provision_id)}
                      className="flex min-h-11 flex-wrap items-baseline gap-x-3 gap-y-1 rounded-sm py-1.5 pl-5 focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
                    >
                      {child.display_label ? (
                        <span className="font-reading font-medium text-action-strong">
                          {child.display_label}
                        </span>
                      ) : null}
                      <span className="font-reading text-ink">
                        {child.heading ?? nodeTypeLabel(child.node_type)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ol>
            ) : null}
          </li>
        ))}
      </ol>
    </>
  );
}
