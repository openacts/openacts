const NODE_TYPE_LABELS: Record<string, string> = {
  document_title: "Title",
  long_title: "Long title",
  arrangement: "Arrangement of sections",
  preamble: "Preamble",
  enacting_formula: "Enacting formula",
  part: "Part",
  chapter: "Chapter",
  division: "Division",
  cross_heading: "Heading",
  section: "Section",
  subsection: "Subsection",
  paragraph: "Paragraph",
  subparagraph: "Subparagraph",
  definition: "Definition",
  schedule: "Schedule",
  schedule_part: "Schedule part",
  schedule_paragraph: "Schedule paragraph",
  schedule_subparagraph: "Schedule subparagraph",
  table: "Table",
  form: "Form",
  authentication: "Authentication",
  explanatory_note: "Explanatory note",
};

const CONTAINER_NODE_TYPES = new Set([
  "chapter",
  "part",
  "division",
  "cross_heading",
  "schedule",
  "schedule_part",
]);

export function nodeTypeLabel(nodeType: string): string {
  return NODE_TYPE_LABELS[nodeType] ?? nodeType.replaceAll("_", " ");
}

export function isContainerNode(nodeType: string): boolean {
  return CONTAINER_NODE_TYPES.has(nodeType);
}

export interface ProvisionHeading {
  display_label: string | null;
  heading: string | null;
  node_type: string;
}

export function provisionTitle(node: ProvisionHeading): string {
  const label = node.display_label?.trim();
  const heading = node.heading?.trim();

  if (label && heading) {
    return `${label} ${heading}`;
  }
  return label ?? heading ?? nodeTypeLabel(node.node_type);
}

export interface ProvisionIdParts {
  actId: string;
  path: string;
}

export function splitProvisionId(provisionId: string): ProvisionIdParts | null {
  const colon = provisionId.indexOf(":");
  if (colon <= 0 || colon === provisionId.length - 1) {
    return null;
  }
  return {
    actId: provisionId.slice(0, colon),
    path: provisionId.slice(colon + 1),
  };
}

export function joinProvisionId(actId: string, path: string): string {
  return `${actId}:${path}`;
}

export function provisionHref(provisionId: string): string {
  const parts = splitProvisionId(provisionId);
  if (!parts) {
    return `/provisions/${encodeURIComponent(provisionId)}`;
  }
  return `/acts/${encodeURIComponent(parts.actId)}/${encodeURIComponent(parts.path)}`;
}

export function actHref(actId: string): string {
  return `/acts/${encodeURIComponent(actId)}`;
}

export function sourceHref(sourceId: string): string {
  return `/sources/${encodeURIComponent(sourceId)}`;
}

export function decodeRouteParam(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function inheritedHeading(
  node: ProvisionHeading,
  ancestors: readonly ProvisionHeading[],
): string | null {
  const own = node.heading?.trim();
  if (own) {
    return own;
  }

  for (let index = ancestors.length - 1; index >= 0; index -= 1) {
    const inherited = ancestors[index]?.heading?.trim();
    if (inherited) {
      return inherited;
    }
  }
  return null;
}
