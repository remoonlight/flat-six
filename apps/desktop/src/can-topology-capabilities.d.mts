type CapabilityNode = {
  id?: string;
  profileId?: string;
  sourceGenerations?: string[];
} | null | undefined;

export function topologyCapability(node: CapabilityNode, adapterModel?: string | null): {
  readable: boolean;
  clearable: boolean;
  engine: boolean;
  referenceOnly: boolean;
  diagnostic: string;
  readEvidence: string;
  coding: string;
  codingDetail: string;
};
export function topologyAdapterProgress(model?: string | null): string;
