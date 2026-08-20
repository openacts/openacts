// Mirrors the closed node_type enum in schemas/provision.schema.json. Unknown
// values pass through readably rather than being hidden, because a corpus that
// gains a node type must not silently render as a blank row.
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

// A container lists its direct children one level down instead of rendering the
// subtree beneath it. Chapter VI of the Constitution has 578 descendants;
// rendering it whole repeats the Act-page problem one level down.
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

// display_label and heading are both source-facing and both nullable. A missing
// label falls back to the node type, never to a fragment of the provision_id.
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

// Decision 0022. A canonical provision_id is `<act_id>:<structural-path>` and
// PROVISION_ID_PATTERN guarantees exactly one colon. Splitting on it is the only
// structure the frontend may assume: it never parses, orders, compares, or reads
// legal meaning from the path.
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

// A malformed id still gets a route: the API rejects it and the page renders
// not-found, which is the same outcome as a Provision that does not exist.
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
