export const READ_PROFILES: Map<string, number>;
export const LIVE_READ_PROFILES: Map<string, number>;
export function liveReadTargets(profileId: string): number[];
export function profileFitsGeneration(profileId: string, generation: string): boolean;
export const FAULT_ONLY_PROFILES: Map<string, number>;
export function faultReadTargets(profileId: string): number[];
export function faultProfileFitsGeneration(profileId: string, generation: string): boolean;
