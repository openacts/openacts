import type { ProvisionRecord } from "./contracts";

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
