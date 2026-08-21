import { notFound, permanentRedirect } from "next/navigation";

import {
  decodeRouteParam,
  provisionHref,
  splitProvisionId,
} from "@/lib/provision";

export default async function ProvisionIdentityRoute({
  params,
}: PageProps<"/provisions/[provisionId]">) {
  const { provisionId } = await params;
  const decoded = decodeRouteParam(provisionId);

  if (decoded === null || splitProvisionId(decoded) === null) {
    notFound();
  }

  permanentRedirect(provisionHref(decoded));
}
