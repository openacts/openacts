import type { ProvisionOutlineItem } from "./contracts";

export interface OutlineSection {
  node: ProvisionOutlineItem;
  children: ProvisionOutlineItem[];
}

// GET /v1/acts/{id}/contents returns every node of the Act: 2487 for the 1999
// Constitution, 647 for the NDPA. Rendering all of them cost 1.5 MB of HTML for
// one Act. The Act page shows the top two levels, 87 and 101 nodes, and hands
// deeper structure to the Provision page.
export const ACT_OUTLINE_MAX_DEPTH = 1;

// The projection guarantees parent_provision_id IS NULL exactly when depth = 0,
// and contents returns the whole Act, so every depth-1 node's parent is present.
// A node that still fails to match is promoted to its own section rather than
// dropped: losing a link to real legal structure is the worse failure.
export function topLevelOutline(
  items: readonly ProvisionOutlineItem[],
): OutlineSection[] {
  const ordered = [...items].sort((a, b) => a.sequence - b.sequence);
  const sections: OutlineSection[] = [];
  const sectionsById = new Map<string, OutlineSection>();

  for (const item of ordered) {
    if (item.depth === 0) {
      const section: OutlineSection = { node: item, children: [] };
      sections.push(section);
      sectionsById.set(item.provision_id, section);
      continue;
    }

    if (item.depth !== ACT_OUTLINE_MAX_DEPTH) {
      continue;
    }

    const parent =
      item.parent_provision_id === null
        ? undefined
        : sectionsById.get(item.parent_provision_id);

    if (parent) {
      parent.children.push(item);
    } else {
      sections.push({ node: item, children: [] });
    }
  }

  return sections;
}

export function outlineNodeCount(sections: readonly OutlineSection[]): number {
  return sections.reduce(
    (total, section) => total + 1 + section.children.length,
    0,
  );
}
