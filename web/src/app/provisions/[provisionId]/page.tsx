import { notFound, permanentRedirect } from "next/navigation";

import {
  decodeRouteParam,
  provisionHref,
  splitProvisionId,
} from "@/lib/provision";

// Decision 0022: the identity route. It exists so a bare opaque identifier
// still resolves, and it permanently redirects to the canonical Act-scoped
// route so the two never compete for indexing.
export default async function ProvisionIdentityRoute({
  params,
}: PageProps<"/provisions/[provisionId]">) {
  const { provisionId } = await params;
  const decoded = decodeRouteParam(provisionId);

  // provisionHref falls back to this same route for a malformed id, which would
  // redirect to itself forever. An id that cannot be split is not addressable.
  if (decoded === null || splitProvisionId(decoded) === null) {
    notFound();
  }

  permanentRedirect(provisionHref(decoded));
}
