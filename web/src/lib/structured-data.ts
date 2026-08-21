import type { ActRecord, ActSummary, ProvisionSummary } from "./contracts";

export interface BreadcrumbStep {
  name: string;
  url: string;
}

export function breadcrumbList(steps: readonly BreadcrumbStep[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: steps.map((step, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: step.name,
      item: step.url,
    })),
  };
}

const LEGAL_FORCE: Record<string, string> = {
  in_force: "https://schema.org/InForce",
  repealed: "https://schema.org/NotInForce",
  not_yet_commenced: "https://schema.org/NotInForce",
  mixed: "https://schema.org/PartiallyInForce",
};

interface LegislationFields {
  "@context": string;
  "@type": string;
  name: string;
  url: string;
  legislationIdentifier: string;
  legislationJurisdiction: string;
  inLanguage: string;
  legislationLegalForce?: string;
  legislationDate?: string;
  isPartOf?: { "@type": string; name: string; url: string };
}

function baseLegislation(
  act: ActSummary,
  url: string,
  name: string,
): LegislationFields {
  const fields: LegislationFields = {
    "@context": "https://schema.org",
    "@type": "Legislation",
    name,
    url,
    legislationIdentifier: act.citation ?? act.act_id,
    legislationJurisdiction: "Nigeria",
    inLanguage: "en",
  };
  const force = LEGAL_FORCE[act.status];
  if (force) {
    fields.legislationLegalForce = force;
  }
  return fields;
}

export function actLegislation(
  act: ActRecord,
  summary: ActSummary,
  url: string,
) {
  const fields = baseLegislation(summary, url, act.titles.official);
  const date = act.dates.commencement?.date ?? act.dates.assent?.date;
  if (date) {
    fields.legislationDate = date;
  }
  return fields;
}

export function provisionLegislation(
  provision: ProvisionSummary,
  provisionName: string,
  act: ActSummary,
  url: string,
  actUrl: string,
) {
  const fields = baseLegislation(act, url, provisionName);
  fields.legislationIdentifier = provision.provision_id;
  fields.isPartOf = {
    "@type": "Legislation",
    name: act.official_title,
    url: actUrl,
  };
  return fields;
}

export function serializeJsonLd(data: unknown): string {
  return JSON.stringify(data)
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e")
    .replaceAll("&", "\\u0026");
}
