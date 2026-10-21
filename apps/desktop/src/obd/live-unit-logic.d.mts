export function mainlandUnit(unit: string | null): { type: string | null; unit: string | null; scale: number; offset: number };
export function mainlandParameter<T>(value: T, unit: string | null): { value: T | number; unit: string | null };
export function mainlandValueText(value: unknown, unit: string | null): { value: unknown; unit: string | null; text: string };
