export function sourceTopologyEcu(profileId: string): number | null;
export function sourceManualScope(scope: unknown, profileId: string | undefined): {generation:string;module:string;namespace:'manufacturer-hex'} | null;
export function withSourceFaultRoutes<T>(generation: T): T;
export function createSourceFaultInvoker(options: { stream: (request: any) => Promise<any>; legacy: (request: any) => Promise<any>; connection: (request: any) => Promise<any>; deviceId: string | null; model: string | null }): (request: any) => Promise<any>;
