import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";

import { RailBlock, ReadingLayout } from "@/components/reading-layout";
import { OpenActsApiError, fetchSource } from "@/lib/api";
import type { ApiResponse, SourceDetailData } from "@/lib/contracts";
import { decodeRouteParam } from "@/lib/provision";

function label(value: string): string {
  return value.replaceAll("_", " ");
}

async function loadSource(
  sourceId: string,
): Promise<ApiResponse<SourceDetailData>> {
  try {
    return await fetchSource(sourceId);
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

function sourceIdFrom(raw: string): string {
  const decoded = decodeRouteParam(raw);
  if (decoded === null) {
    notFound();
  }
  return decoded;
}

export async function generateMetadata({
  params,
}: PageProps<"/sources/[sourceId]">): Promise<Metadata> {
  const { sourceId } = await params;
  const { data } = await loadSource(sourceIdFrom(sourceId));
  return {
    title: data.source.document_title ?? "Source",
    description: `Provenance and recorded retrieval locations for this Source.`,
  };
}

export default async function SourcePage({
  params,
}: PageProps<"/sources/[sourceId]">) {
  await connection();

  const { sourceId } = await params;
  const response = await loadSource(sourceIdFrom(sourceId));
  const source = response.data.source;
  const publication = source.publication;

  const facts: [string, string][] = [
    ["Source class", label(source.source_class)],
    ["Media type", source.media_type],
    ["Pages", source.page_count.toLocaleString("en-NG")],
    ["Byte length", source.byte_length.toLocaleString("en-NG")],
    ["Text layer", label(source.text_layer)],
    ["Language", source.language ?? "not recorded"],
    [
      "Publication",
      publication?.name
        ? [publication.name, publication.number, publication.date]
            .filter(Boolean)
            .join(" · ")
        : "not recorded",
    ],
    ["Redistribution", label(source.redistribution.status)],
  ];

  return (
    <main id="main-content">
      <section className="mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
        <ReadingLayout
          provenance={
            <>
              <RailBlock title="Digest">
                <span className="[overflow-wrap:anywhere] text-ink">
                  {source.source_id}
                </span>
              </RailBlock>
              <RailBlock title="Release">
                {response.meta.corpus_release ?? "unidentified"}
              </RailBlock>
              <RailBlock title="Hosting">
                OpenActs does not host this document. Links go to the publisher.
              </RailBlock>
            </>
          }
        >
          <p className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
            Source
          </p>
          <h1 className="mt-1 max-w-[28ch] font-reading text-[clamp(1.75rem,1.3rem+1.6vw,2.375rem)] font-medium leading-[1.2] text-ink">
            {source.document_title ?? "Untitled source"}
          </h1>
          {source.document_publisher ? (
            <p className="mt-3 text-[0.9375rem] text-muted">
              {source.document_publisher}
            </p>
          ) : null}

          <dl className="mt-8 border-t border-line">
            {facts.map(([key, value]) => (
              <div
                key={key}
                className="flex flex-wrap gap-x-8 gap-y-1 border-b border-line py-2.5"
              >
                <dt className="w-44 flex-none text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
                  {key}
                </dt>
                <dd className="id m-0 flex-1 text-ink">{value}</dd>
              </div>
            ))}
          </dl>

          <h2 className="mt-12 font-reading text-2xl font-medium text-ink">
            Retrieval locations
          </h2>
          <ul className="mt-4 border-t border-line">
            {source.locations.map((location) => (
              <li key={location.url} className="border-b border-line py-5">
                <a
                  href={location.url}
                  rel="nofollow noopener external"
                  className="id rounded-sm text-action underline [overflow-wrap:anywhere] hover:text-action-strong focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
                >
                  {location.url}
                </a>
                <p className="id mt-1.5 text-muted">
                  {location.provider_name ?? "Provider not recorded"}
                  {location.retrieved_at
                    ? ` · retrieved ${location.retrieved_at.slice(0, 10)}`
                    : ""}
                  {location.http_last_modified
                    ? ` · last modified ${location.http_last_modified.slice(0, 10)}`
                    : ""}
                </p>
                {location.notes ? (
                  <p className="law mt-2 text-[1rem] text-ink">{location.notes}</p>
                ) : null}
              </li>
            ))}
          </ul>

          {source.document_notes.length > 0 ? (
            <>
              <h2 className="mt-12 font-reading text-2xl font-medium text-ink">
                Document notes
              </h2>
              <div className="mt-3 space-y-2">
                {source.document_notes.map((note) => (
                  <p key={note} className="law text-[1rem] text-ink">
                    {note}
                  </p>
                ))}
              </div>
            </>
          ) : null}
        </ReadingLayout>
      </section>
    </main>
  );
}
