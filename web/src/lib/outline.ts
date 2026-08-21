import type { ProvisionOutlineItem } from "./contracts";

export interface OutlineSection {
  node: ProvisionOutlineItem;
  children: ProvisionOutlineItem[];
}

export const ACT_OUTLINE_MAX_DEPTH = 1;

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
