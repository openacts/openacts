import Link from "next/link";

import { OpenActsApiError, fetchActContents } from "@/lib/api";
import type { ProvisionOutlineItem, ProvisionSummary } from "@/lib/contracts";
import { nodeTypeLabel, provisionHref, provisionTitle } from "@/lib/provision";

function UpToParentIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h11a5 5 0 0 1 5 5v6" />
    </svg>
  );
}

function shortLabel(node: ProvisionOutlineItem): string {
  return node.display_label ?? node.heading ?? nodeTypeLabel(node.node_type);
}

async function loadSiblings(
  actId: string,
  parent: ProvisionSummary | null,
): Promise<ProvisionOutlineItem[] | null> {
  try {
    const response = parent
      ? await fetchActContents(actId, parent.provision_id)
      : await fetchActContents(actId, undefined, 0);
    return response.data.items;
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
    return null;
  }
}

export function ProvisionNavSkeleton() {
  return (
    <div aria-hidden="true" className="flex gap-2 lg:flex-col lg:gap-1.5">
      {[0, 1, 2, 3, 4].map((placeholder) => (
        <span
          key={placeholder}
          className="h-8 w-16 flex-none animate-pulse rounded-sm bg-line motion-reduce:animate-none lg:w-full"
        />
      ))}
    </div>
  );
}

export async function ProvisionNav({
  actId,
  provisionId,
  parent,
}: {
  actId: string;
  provisionId: string;
  parent: ProvisionSummary | null;
}) {
  const siblings = await loadSiblings(actId, parent);

  if (!siblings || siblings.length < 2) {
    return null;
  }

  const currentIndex = siblings.findIndex(
    (sibling) => sibling.provision_id === provisionId,
  );

  return (
    <nav
      aria-label={
        parent
          ? `Provisions in ${provisionTitle(parent)}`
          : "Provisions in this Act"
      }
      className="lg:sticky lg:top-8"
    >
      <div className="mb-3 hidden items-center gap-2 lg:flex">
        {parent ? (
          <Link
            href={provisionHref(parent.provision_id)}
            title={`Up to ${provisionTitle(parent)}`}
            aria-label={`Up to ${provisionTitle(parent)}`}
            className="inline-flex flex-none items-center rounded-sm p-1 text-muted hover:text-action-strong focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            <UpToParentIcon />
          </Link>
        ) : null}
        <span className="id flex-1 text-muted">
          {currentIndex + 1}/{siblings.length}
        </span>
      </div>
      <ol className="flex gap-1.5 overflow-x-auto pb-2 lg:flex-col lg:gap-0 lg:overflow-visible lg:pb-0">
        {siblings.map((sibling) => {
          const isCurrent = sibling.provision_id === provisionId;
          return (
            <li key={sibling.provision_id} className="flex-none lg:flex-auto">
              <Link
                href={provisionHref(sibling.provision_id)}
                aria-current={isCurrent ? "page" : undefined}
                title={provisionTitle(sibling)}
                className={`block min-h-11 truncate rounded-sm px-2.5 py-3 text-sm leading-5 focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 lg:min-h-0 lg:border-l lg:py-1.5 ${
                  isCurrent
                    ? "border-action bg-surface font-semibold text-action-strong lg:border-l-2"
                    : "border-line text-muted hover:text-ink"
                }`}
              >
                <span className="font-reading">{shortLabel(sibling)}</span>
                {sibling.heading && sibling.display_label ? (
                  <span className="ml-2 hidden text-[0.8125rem] lg:inline">
                    {sibling.heading}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
