import { createHash, timingSafeEqual } from "node:crypto";
import { revalidateTag } from "next/cache";

import { CORPUS_TAG } from "@/lib/api";

// Decision 0024. This is the single Route Handler the frontend has, and the
// only one it may have. It exists to forget cached corpus reads after a release
// is activated. It reads no corpus data, proxies nothing, and returns no legal
// text. That boundary is the whole reason it is permitted.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SECRET_HEADER = "x-openacts-revalidate-secret";
const MAX_TAGS = 64;
const MAX_TAG_LENGTH = 128;

// Hashing first gives timingSafeEqual two equal-length buffers, so the
// comparison leaks neither the secret nor its length.
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
  // An explicit but unusable list purges everything rather than nothing: a
  // release is live either way, and serving it stale is the worse failure.
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
    // No body means purge everything, which is what activation wants.
  }

  const tags = requestedTags(body);
  for (const tag of tags) {
    // Corpus reads are stored with no expiry, so "max" is the profile whose
    // entries this purges. Nothing here expires on a timer by design.
    revalidateTag(tag, "max");
  }

  return Response.json({ purged: tags });
}
