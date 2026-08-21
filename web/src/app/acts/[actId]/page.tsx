import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  ActArrangement,
  ActArrangementSkeleton,
} from "@/components/act-arrangement";
import { CopyButton } from "@/components/copy-button";
import { JsonLd } from "@/components/json-ld";
import { RailBlock, ReadingLayout } from "@/components/reading-layout";
import { actTextKind, textKindLabel } from "@/lib/act";
import { OpenActsApiError, fetchActDetail } from "@/lib/api";
import type { ActDateKind, ActDetail, ApiResponse } from "@/lib/contracts";
import { actHref, decodeRouteParam, sourceHref } from "@/lib/provision";
import { absoluteUrl } from "@/lib/site";
import { actLegislation, breadcrumbList } from "@/lib/structured-data";

const DATE_LABELS: Record<ActDateKind, string> = {
  assent: "Assent",
  publication: "Publication",
  commencement: "Commencement",
  repeal: "Repeal",
};

async function loadAct(actId: string): Promise<ApiResponse<ActDetail>> {
  try {
    return await fetchActDetail(actId);
  } catch (error) {
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
}: PageProps<"/acts/[actId]">): Promise<Metadata> {
  const { actId } = await params;
  const { data } = await loadAct(decodeRouteParam(actId) ?? actId);
  const url = absoluteUrl(actHref(decodeRouteParam(actId) ?? actId));
  const description = data.act.titles.long ?? data.act.titles.official;
  return {
    title: data.act.titles.official,
    description,
    alternates: { canonical: url },
    openGraph: {
      type: "article",
      title: data.act.titles.official,
      description,
      url,
      siteName: "OpenActs",
    },
  };
}

export default async function ActDetailPage({
  params,
}: PageProps<"/acts/[actId]">) {

  const { actId } = await params;
  const decodedActId = decodeRouteParam(actId);
  if (decodedActId === null) {
    notFound();
  }

  const detail = await loadAct(decodedActId);
  const { act, sources } = detail.data;
  const url = absoluteUrl(actHref(decodedActId));
  const summary = {
    act_id: act.act_id,
    official_title: act.titles.official,
    short_title: act.titles.short,
    year: act.year,
    number: act.number,
    citation: act.citation,
    text_kind: actTextKind(act),
    status: act.status,
    checked_through_date: act.checked_through_date,
  };

  return (
    <main id="main-content">
      <JsonLd data={actLegislation(act, summary, url)} />
      <JsonLd
        data={breadcrumbList([
          { name: "Acts", url: absoluteUrl("/acts") },
          { name: act.titles.official, url },
        ])}
      />
      <section className="mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
        <Link
          href="/acts"
          className="id rounded-sm text-muted underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
        >
          &larr; All Acts
        </Link>

        <div className="mt-6">
          <ReadingLayout
            provenance={
              <>
                <RailBlock title="Text">
                  {textKindLabel(actTextKind(act)).toLowerCase()}
                  <br />
                  status {act.status}
                  <br />
                  {act.checked_through_date
                    ? `checked through ${act.checked_through_date}`
                    : "currentness not established"}
                </RailBlock>
                <RailBlock title="Citation">
                  <span className="text-ink">
                    {act.citation ?? "No citation recorded"}
                  </span>
                </RailBlock>
                <RailBlock title="Dates">
                  {(Object.keys(DATE_LABELS) as ActDateKind[]).map((kind) => (
                    <span key={kind} className="block">
                      {DATE_LABELS[kind].toLowerCase()}:{" "}
                      {act.dates[kind]?.date ?? "not recorded"}
                    </span>
                  ))}
                </RailBlock>
                <RailBlock title={`Sources (${sources.length})`}>
                  {sources.map((source) => (
                    <Link
                      key={source.source_id}
                      href={sourceHref(source.source_id)}
                      className="block rounded-sm underline focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
                    >
                      {source.document_title ?? "Untitled source"}
                    </Link>
                  ))}
                </RailBlock>
              </>
            }
          >
            <h1 className="max-w-[20ch] font-reading text-[clamp(2rem,1.5rem+2vw,2.75rem)] font-medium leading-[1.15] text-ink">
              {act.titles.official}
            </h1>
            {act.titles.short && act.titles.short !== act.titles.official ? (
              <p className="mt-3 text-[0.9375rem] text-muted">
                {act.titles.short}
              </p>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-x-6">
              <CopyButton value={act.citation ?? act.titles.official} label="Copy citation" />
            </div>

            {act.titles.long ? (
              <p className="law mt-8 text-ink">{act.titles.long}</p>
            ) : null}

            <h2 className="mt-12 font-reading text-2xl font-medium text-ink">
              Arrangement
            </h2>
            <Suspense fallback={<ActArrangementSkeleton />}>
              <ActArrangement
                actId={decodedActId}
                actRelease={detail.meta.corpus_release}
              />
            </Suspense>
          </ReadingLayout>
        </div>
      </section>
    </main>
  );
}
