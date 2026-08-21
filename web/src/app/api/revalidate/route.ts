import { createHash, timingSafeEqual } from "node:crypto";
import { revalidateTag } from "next/cache";

import { CORPUS_TAG } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SECRET_HEADER = "x-openacts-revalidate-secret";
const MAX_TAGS = 64;
const MAX_TAG_LENGTH = 128;

function secretMatches(provided: string, expected: string): boolean {
  return timingSafeEqual(
    createHash("sha256").update(provided).digest(),
    createHash("sha256").update(expected).digest(),
  );
}

function requestedTags(body: unknown): string[] {
  if (!body || typeof body !== "object" || !("tags" in body)) {
    return [CORPUS_TAG];
  }
  const { tags } = body;
  if (!Array.isArray(tags)) {
    return [CORPUS_TAG];
  }
  const usable = tags.filter(
    (tag): tag is string =>
      typeof tag === "string" && tag.length > 0 && tag.length <= MAX_TAG_LENGTH,
  );
  return usable.length > 0 ? usable.slice(0, MAX_TAGS) : [CORPUS_TAG];
}

export async function POST(request: Request): Promise<Response> {
  const expected = process.env.OPENACTS_REVALIDATE_SECRET;
  if (!expected) {
    return Response.json(
      { error: "revalidation is not configured" },
      { status: 503 },
    );
  }

  const provided = request.headers.get(SECRET_HEADER);
  if (!provided || !secretMatches(provided, expected)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
  }

  const tags = requestedTags(body);
  for (const tag of tags) {
    revalidateTag(tag, "max");
  }

  return Response.json({ purged: tags });
}
