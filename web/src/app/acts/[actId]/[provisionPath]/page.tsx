import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { ContentBlocks } from "@/components/content/content-blocks";
import { CopyButton } from "@/components/copy-button";
import { LawBlock } from "@/components/law-block";
import { CurrentnessLine, SourceFootnote } from "@/components/provenance";
import {
  ProvisionNav,
  ProvisionNavSkeleton,
} from "@/components/provision-nav";
import { OpenActsApiError, fetchProvision } from "@/lib/api";
import type {
  ApiResponse,
  ProvisionDetail,
  ProvisionRecord,
  ProvisionSummary,
} from "@/lib/contracts";
import {
  actHref,
  decodeRouteParam,
  inheritedHeading,
  isContainerNode,
  joinProvisionId,
  nodeTypeLabel,
  provisionHref,
  provisionTitle,
} from "@/lib/provision";
import { directChildren, relativeDepths } from "@/lib/reading";

// Route params arrive percent-encoded; both halves are decoded before being
// rejoined into the canonical identifier the API expects.
function provisionIdFrom(actId: string, provisionPath: string): string {
  const act = decodeRouteParam(actId);
  const path = decodeRouteParam(provisionPath);
  if (act === null || path === null) {
    notFound();
  }
  return joinProvisionId(act, path);
}

async function loadProvision(
  provisionId: string,
): Promise<ApiResponse<ProvisionDetail>> {
  try {
    return await fetchProvision(provisionId);
  } catch (error) {
    // A malformed id is a 400 and an absent one a 404; neither is addressable,
    // so both render the application not-found page.
    if (
      error instanceof OpenActsApiError &&
      error.status >= 400 &&
      error.status < 500
    ) {
      notFound();
    }
    throw error;
  }
}

export async function generateMetadata({
  params,
}: PageProps<"/acts/[actId]/[provisionPath]">): Promise<Metadata> {
  const { actId, provisionPath } = await params;
  const { data } = await loadProvision(provisionIdFrom(actId, provisionPath));
  const title = provisionTitle(data.provision);
  return {
    title: `${title} — ${data.act.official_title}`,
    description: `${title} of the ${data.act.official_title}, with its Source and text fidelity.`,
    alternates: {
      canonical: provisionHref(data.provision.provision_id),
    },
  };
}

function NavLink({
  summary,
  rel,
  label,
  align,
}: {
  summary: ProvisionSummary;
  rel: "prev" | "next";
  label: string;
  align: string;
}) {
  return (
    <Link
      href={provisionHref(summary.provision_id)}
      rel={rel}
      className={`max-w-[18rem] rounded-sm focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 ${align}`}
    >
      <span className="block text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
        {label}
      </span>
      <span className="font-reading text-ink">{provisionTitle(summary)}</span>
    </Link>
  );
}

function ChildIndex({ nodes }: { nodes: ProvisionRecord[] }) {
  return (
    <ol className="mt-6 border-t border-line">
      {nodes.map((child) => (
        <li key={child.provision_id} className="border-b border-line">
          <Link
            href={provisionHref(child.provision_id)}
            className="flex min-h-11 flex-wrap items-baseline gap-x-3 gap-y-1 rounded-sm py-4 focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
          >
            {child.display_label ? (
              <span className="font-reading text-[1.1875rem] font-medium text-action-strong">
                {child.display_label}
              </span>
            ) : null}
            <span className="font-reading text-[1.1875rem] text-ink">
              {child.heading ?? nodeTypeLabel(child.node_type)}
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

export default async function ProvisionPage({
  params,
}: PageProps<"/acts/[actId]/[provisionPath]">) {
  await connection();

  const { actId, provisionPath } = await params;
  const detail = await loadProvision(provisionIdFrom(actId, provisionPath));
  const { act, provision, descendants, ancestors, navigation, sources, citations } =
    detail.data;

  const isContainer = isContainerNode(provision.node_type);
  const children = isContainer ? directChildren(provision.provision_id, descendants) : [];
  const depths = relativeDepths(provision.provision_id, descendants);

  return (
    <main id="main-content">
      <section className="mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
        <nav aria-label="Breadcrumb" className="id flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
          <Link href={actHref(act.act_id)} className="rounded-sm underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2">
            {act.official_title}
          </Link>
          {ancestors.map((ancestor) => (
            <span key={ancestor.provision_id} className="flex items-center gap-2">
              <span aria-hidden="true">&rsaquo;</span>
              <Link href={provisionHref(ancestor.provision_id)} className="rounded-sm underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2">
                {provisionTitle(ancestor)}
              </Link>
            </span>
          ))}
        </nav>

        <div className="mt-6 grid gap-x-10 gap-y-8 lg:grid-cols-[13rem_minmax(0,1fr)]">
          {/* Sibling navigation streams: the legal text never waits on the
              Act outline, which is a second and much larger request. */}
          <div className="min-w-0 lg:col-start-1">
            <Suspense fallback={<ProvisionNavSkeleton />}>
              <ProvisionNav
                actId={act.act_id}
                provisionId={provision.provision_id}
                parent={ancestors.at(-1) ?? null}
              />
            </Suspense>
          </div>

          <div className="min-w-0 lg:col-start-2">
            {/* Padded to the same left edge as the reading column below, so the
                title, the hairline and the wording share one alignment. */}
            <div className="lg:ml-20 lg:border-l lg:border-transparent lg:pl-7">
              <p className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
                {nodeTypeLabel(provision.node_type)}
              </p>
              <h1 className="mt-1 max-w-[36rem] text-balance font-reading text-[clamp(2rem,1.5rem+2vw,2.75rem)] font-medium leading-[1.15] text-ink">
                {provision.display_label ? (
                  <span className="text-action-strong">
                    {provision.display_label}{" "}
                  </span>
                ) : null}
                {inheritedHeading(provision, ancestors) ??
                  nodeTypeLabel(provision.node_type)}
              </h1>
              <CurrentnessLine act={act} />
              <div className="mt-3 flex flex-wrap gap-x-6">
                <CopyButton
                  value={`${provisionTitle(provision)}, ${act.official_title}`}
                  label="Copy citation"
                />
              </div>
            </div>

            <div className="mt-8">
              {provision.content_blocks.length > 0 ? (
                <LawBlock label={null}>
                  <ContentBlocks
                    blocks={provision.content_blocks}
                    provisionId={provision.provision_id}
                    citations={citations}
                  />
                </LawBlock>
              ) : null}

              {isContainer ? (
                children.length > 0 ? (
                  <div className="lg:ml-20 lg:pl-7">
                    <ChildIndex nodes={children} />
                  </div>
                ) : provision.content_blocks.length === 0 ? (
                  <p className="text-[0.9375rem] text-muted lg:ml-20 lg:pl-7">
                    Nothing is recorded beneath this{" "}
                    {nodeTypeLabel(provision.node_type).toLowerCase()} yet.
                  </p>
                ) : null
              ) : (
                descendants.map((node) => (
                  <div
                    key={node.provision_id}
                    style={{
                      marginLeft: `${Math.min((depths.get(node.provision_id) ?? 1) - 1, 3) * 1.25}rem`,
                    }}
                  >
                    <LawBlock label={node.display_label}>
                      {node.heading ? (
                        <h2 className="mb-1 font-reading text-[1.1875rem] font-medium text-ink">
                          {node.heading}
                        </h2>
                      ) : null}
                      <ContentBlocks
                        blocks={node.content_blocks}
                        provisionId={node.provision_id}
                        citations={citations}
                      />
                    </LawBlock>
                  </div>
                ))
              )}
            </div>

            <div className="lg:ml-20 lg:pl-7">
              <SourceFootnote
                sources={sources}
                spans={provision.source_spans}
                fidelity={provision.text_fidelity}
                release={detail.meta.corpus_release}
              />

              {navigation.previous || navigation.next ? (
                <nav
                  aria-label="Document order"
                  className="mt-8 flex justify-between gap-8"
                >
                  {navigation.previous ? (
                    <NavLink summary={navigation.previous} rel="prev" label="Previous in document order" align="" />
                  ) : (
                    <span />
                  )}
                  {navigation.next ? (
                    <NavLink summary={navigation.next} rel="next" label="Next in document order" align="text-right" />
                  ) : (
                    <span />
                  )}
                </nav>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
