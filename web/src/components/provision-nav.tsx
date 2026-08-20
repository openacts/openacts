import Link from "next/link";

import { OpenActsApiError, fetchActContents } from "@/lib/api";
import type { ProvisionOutlineItem } from "@/lib/contracts";
import { nodeTypeLabel, provisionHref, provisionTitle } from "@/lib/provision";
import { siblingContext } from "@/lib/reading";

function shortLabel(node: ProvisionOutlineItem): string {
  return node.display_label ?? node.heading ?? nodeTypeLabel(node.node_type);
}

// Sibling navigation is context, not content: if the outline cannot be read the
// page still renders its legal text, which is the part that matters.
async function loadOutline(
  actId: string,
): Promise<ProvisionOutlineItem[] | null> {
  try {
    const response = await fetchActContents(actId);
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
}: {
  actId: string;
  provisionId: string;
}) {
  const items = await loadOutline(actId);
  if (!items) {
    return null;
  }

  const context = siblingContext(items, provisionId);
  // One sibling is the Provision itself; there is nothing to navigate between.
  if (!context || context.siblings.length < 2) {
    return null;
  }

  const { parent, siblings, currentIndex } = context;

  return (
    <nav
      aria-label={
        parent
          ? `Provisions in ${provisionTitle(parent)}`
          : "Provisions in this Act"
      }
      className="lg:sticky lg:top-8"
    >
      {/* Parent and position share one line. The parent truncates because a
          heading is arbitrarily long and the rail is not; the full text stays
          in the accessibility tree, since truncation here is purely visual. */}
      <div className="mb-3 hidden items-baseline gap-2 lg:flex">
        {parent ? (
          <Link
            href={provisionHref(parent.provision_id)}
            title={provisionTitle(parent)}
            className="id min-w-0 flex-1 truncate rounded-sm text-muted underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            &uarr; {provisionTitle(parent)}
          </Link>
        ) : (
          <span className="id min-w-0 flex-1 truncate text-muted">
            This Act
          </span>
        )}
        <span className="id flex-none text-muted">
          {currentIndex + 1}/{siblings.length}
        </span>
      </div>
      {/* One list, two shapes: a scrolling strip of labels on narrow screens,
          a vertical rail from lg up. Nothing is duplicated or hidden. */}
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
