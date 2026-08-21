import type { ProvisionRecord } from "./contracts";

export function directChildren(
  provisionId: string,
  descendants: readonly ProvisionRecord[],
): ProvisionRecord[] {
  return descendants.filter(
    (node) => node.parent_provision_id === provisionId,
  );
}

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
