import type { ProvisionOutlineItem, ProvisionRecord } from "./contracts";

// A container lists one level. The API returns the whole subtree, so direct
// children are selected here rather than fetched separately.
export function directChildren(
  provisionId: string,
  descendants: readonly ProvisionRecord[],
): ProvisionRecord[] {
  return descendants.filter(
    (node) => node.parent_provision_id === provisionId,
  );
}

// Descendants arrive in preorder, so a parent is always seen before its
// children and one pass is enough. Depth is relative to the requested
// Provision, which is 0; anything whose parent is outside the subtree starts
// at 1 rather than being dropped.
export function relativeDepths(
  provisionId: string,
  descendants: readonly ProvisionRecord[],
): Map<string, number> {
  const depths = new Map<string, number>([[provisionId, 0]]);

  for (const node of descendants) {
    const parent = node.parent_provision_id;
    const parentDepth = parent === null ? undefined : depths.get(parent);
    depths.set(node.provision_id, parentDepth === undefined ? 1 : parentDepth + 1);
  }

  return depths;
}

export interface SiblingContext {
  parent: ProvisionOutlineItem | null;
  siblings: ProvisionOutlineItem[];
  currentIndex: number;
}

// The Provision endpoint returns ancestors and document-order navigation, but
// never siblings, so the reader derives them from the Act outline. Nodes are
// matched on parent_provision_id; a root Provision's siblings are the other
// roots, which the projection guarantees have a null parent.
export function siblingContext(
  items: readonly ProvisionOutlineItem[],
  provisionId: string,
): SiblingContext | null {
  const current = items.find((item) => item.provision_id === provisionId);
  if (!current) {
    return null;
  }

  const siblings = items
    .filter((item) => item.parent_provision_id === current.parent_provision_id)
    .sort((a, b) => a.sequence - b.sequence);

  return {
    parent:
      items.find(
        (item) => item.provision_id === current.parent_provision_id,
      ) ?? null,
    siblings,
    currentIndex: siblings.findIndex(
      (item) => item.provision_id === provisionId,
    ),
  };
}
