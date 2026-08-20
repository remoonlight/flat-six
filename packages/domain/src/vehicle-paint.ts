/**
 * 981 Boxster/Cayman (2012–2016) factory brochure palette.
 * 4 solid + 8 metallic + 3 special; 4 soft tops; interiors per sales brochure.
 * Keep in sync with data/seed/vehicle/paint-palette.json.
 */

export type SpecColor = {
  /** Factory / PET / build-sheet code */
  code: string;
  en: string;
  zh: string;
  hex: string;
  /** Optional Lack-style code for sticker autofill (e.g. L041). */
  oem?: string;
  /** Optional order / sales code (e.g. PX on GNG). */
  order?: string;
  group?:
    | "solid"
    | "metallic"
    | "special"
    | "leatherette"
    | "leatherette_two_tone"
    | "leather"
    | "leather_two_tone";
};

/** Exterior — brochure: 4 solid + 8 metallic + 3 special. */
export const PAINT_OPTIONS = [
  // Solid
  { code: "041", en: "Black", zh: "黑色", hex: "#1A1A1C", oem: "L041", group: "solid" },
  { code: "C9A", en: "White", zh: "白色", hex: "#F5F5F2", group: "solid" },
  { code: "84A", en: "Guards Red", zh: "卫士红", hex: "#A01818", oem: "L84A", group: "solid" },
  { code: "1S1", en: "Racing Yellow", zh: "赛车黄", hex: "#F0D010", oem: "L1S1", group: "solid" },
  // Metallic
  { code: "C9X", en: "Jet Black Metallic", zh: "墨岩黑", hex: "#0E0E10", oem: "LC9X", group: "metallic" },
  { code: "M7S", en: "Agate Grey Metallic", zh: "玛瑙灰", hex: "#5A5E64", oem: "LM7S", group: "metallic" },
  // PETKA sales G0 = Anthracite Brown / formula M8S
  { code: "G0", en: "Anthracite Brown Metallic", zh: "烟棕色", hex: "#3A2A22", oem: "LM8S", group: "metallic" },
  { code: "M8Y", en: "Mahogany Metallic", zh: "桃花心木", hex: "#4A2820", oem: "LM8Y", group: "metallic" },
  { code: "M5X", en: "Dark Blue Metallic", zh: "深蓝", hex: "#1A2848", oem: "LM5X", group: "metallic" },
  { code: "M5J", en: "Sapphire Blue Metallic", zh: "宝石蓝", hex: "#1E4A7A", oem: "LM5J", group: "metallic" },
  { code: "M7U", en: "Rhodium Silver Metallic", zh: "铑银", hex: "#C8CCD2", oem: "LM7U", group: "metallic" },
  { code: "S9R", en: "Carrara White Metallic", zh: "Carrera白", hex: "#E8E8E4", oem: "LS9R", group: "metallic" },
  // Special
  { code: "M3C", en: "Carmine Red", zh: "胭脂红", hex: "#8A1018", oem: "LM3C", group: "special" },
  { code: "5P1", en: "Lime Gold Metallic", zh: "青柠金", hex: "#8A8A4A", oem: "L5P1", group: "special" },
  { code: "M7Z", en: "GT Silver Metallic", zh: "GT银", hex: "#A8ACB0", oem: "LM7Z", group: "special" },
] as const satisfies readonly SpecColor[];

/**
 * Soft tops — A27 Black / G25 blue / L25 red / R25 brown (order 6V).
 */
export const TOP_OPTIONS_SPEC = [
  { code: "A27", en: "Black", zh: "黑色", hex: "#1A1A1C" },
  { code: "G25", en: "blue", zh: "蓝色", hex: "#1E3A5A" },
  { code: "L25", en: "red", zh: "红色", hex: "#6A1820" },
  { code: "R25", en: "brown", zh: "棕色", hex: "#5A4030", order: "6V" },
] as const satisfies readonly SpecColor[];

/**
 * Interior — PET / PETKA trim codes.
 * leatherette · leatherette two-tone · leather · leather two-tone.
 */
export const INTERIOR_OPTIONS_SPEC = [
  // 人造革
  { code: "DK4", en: "Black", zh: "黑色", hex: "#1A1A1C", group: "leatherette" },
  { code: "DJ6", en: "Platinum Grey", zh: "铂金灰", hex: "#8A8E94", group: "leatherette" },
  { code: "DM5", en: "Luxor beige", zh: "卢克索米色", hex: "#C8B8A0", group: "leatherette" },
  { code: "OV5", en: "yachting blue", zh: "游艇蓝", hex: "#1E3A5A", group: "leatherette" },
  // 人造革/真皮套装 双色
  {
    code: "GMJ",
    en: "Agate Gray/Pebble Grey",
    zh: "玛瑙灰/卵石灰",
    hex: "#5A5E64",
    group: "leatherette_two_tone",
  },
  {
    code: "GNJ",
    en: "Agate Gray/Amber",
    zh: "玛瑙灰/琥珀色",
    hex: "#5A5E64",
    group: "leatherette_two_tone",
  },
  {
    code: "GPJ",
    en: "Agate Gray/Lime Gold",
    zh: "玛瑙灰/柠檬金",
    hex: "#8A8A4A",
    group: "leatherette_two_tone",
  },
  // 真皮
  { code: "A11", en: "Black", zh: "黑色", hex: "#1A1A1C", group: "leather" },
  { code: "9B0", en: "Platinum Grey", zh: "铂金灰", hex: "#8A8E94", group: "leather" },
  { code: "7J0", en: "Luxor beige", zh: "卢克索米色", hex: "#C8B8A0", group: "leather" },
  { code: "6A0", en: "yachting blue", zh: "游艇蓝", hex: "#1E3A5A", group: "leather" },
  { code: "OA1", en: "Agate Gray", zh: "玛瑙灰", hex: "#5A5E64", group: "leather" },
  { code: "6J0", en: "Espresso", zh: "深咖啡色", hex: "#3A2820", group: "leather" },
  { code: "N12", en: "Carrera Red", zh: "Carrera红", hex: "#8A1820", group: "leather" },
  { code: "DSP", en: "Alcantara Black", zh: "Alcantara 黑色", hex: "#141416", group: "leather" },
  // 真皮双色
  {
    code: "GMG",
    en: "Agate Gray/Pebble Grey",
    zh: "玛瑙灰/卵石灰",
    hex: "#5A5E64",
    group: "leather_two_tone",
  },
  {
    code: "GNG",
    en: "Agate Gray/Amber",
    zh: "玛瑙灰/琥珀色",
    hex: "#5A5E64",
    order: "PX",
    group: "leather_two_tone",
  },
  {
    code: "GPG",
    en: "Agate Gray/Lime Gold",
    zh: "玛瑙灰/柠檬金",
    hex: "#8A8A4A",
    group: "leather_two_tone",
  },
  {
    code: "DTO",
    en: "Black/Luxor beige",
    zh: "黑色/卢克索米色",
    hex: "#1A1A1C",
    group: "leather_two_tone",
  },
  {
    code: "DJS",
    en: "Alcantara Black/Rhodium Silver",
    zh: "Alcantara 黑色/铑银色",
    hex: "#141416",
    group: "leather_two_tone",
  },
  {
    code: "DSN",
    en: "Alcantara Black/Carmine Red",
    zh: "Alcantara 黑色/胭脂红",
    hex: "#141416",
    group: "leather_two_tone",
  },
] as const satisfies readonly SpecColor[];

export type PaintName = (typeof PAINT_OPTIONS)[number]["en"];
export type TopOption = (typeof TOP_OPTIONS_SPEC)[number]["code"];
export type InteriorOption = (typeof INTERIOR_OPTIONS_SPEC)[number]["code"];

export const TOP_OPTIONS = TOP_OPTIONS_SPEC.map((t) => t.code) as unknown as readonly TopOption[];
export const INTERIOR_OPTIONS = INTERIOR_OPTIONS_SPEC.map(
  (i) => i.code,
) as unknown as readonly InteriorOption[];

export const DEFAULT_PAINT_NAME: PaintName = "Mahogany Metallic";
export const DEFAULT_TOP_OPTION: TopOption = "A27";
export const DEFAULT_INTERIOR_OPTION: InteriorOption = "A11";

const PAINT_BY_EN = new Map(PAINT_OPTIONS.map((p) => [p.en.toUpperCase(), p]));
const PAINT_BY_CODE = new Map(PAINT_OPTIONS.map((p) => [p.code.toUpperCase(), p]));
const TOP_BY_CODE = new Map(TOP_OPTIONS_SPEC.map((t) => [t.code.toUpperCase(), t]));
const INT_BY_CODE = new Map(INTERIOR_OPTIONS_SPEC.map((i) => [i.code.toUpperCase(), i]));

/** Prior soft-top / PR codes → current PET trim codes. */
const TOP_ALIASES: Record<string, TopOption> = {
  "4V": "A27",
  BLUE: "G25",
  RED: "L25",
  "6V": "R25",
  BROWN: "R25",
};

const PAINT_ALIASES: Record<string, PaintName> = {
  M8S: "Anthracite Brown Metallic",
  N0: "Agate Grey Metallic",
  U2: "GT Silver Metallic",
  "0Q": "White",
};

/** Prior brochure / invented codes → current PET trim codes. */
const INT_ALIASES: Record<string, InteriorOption> = {
  AJ: "A11",
  CD: "9B0",
  UD: "7J0",
  ED: "6A0",
  BV: "OA1",
  NE: "6J0",
  NG: "N12",
  "970-BX": "GNG",
  "970-LG": "GPG",
  "970-PG": "GMG",
  PX: "GNG",
  "AJ-CR": "DSN",
  "AJ-RS": "DJS",
};

function paintEntry(nameOrCode: string | null | undefined) {
  const raw = nameOrCode?.trim() || DEFAULT_PAINT_NAME;
  const upper = raw.toUpperCase();
  const aliased = PAINT_ALIASES[upper];
  return (
    PAINT_BY_EN.get(upper) ??
    PAINT_BY_CODE.get(upper) ??
    (aliased ? PAINT_BY_EN.get(aliased.toUpperCase()) : undefined) ??
    PAINT_BY_EN.get(DEFAULT_PAINT_NAME.toUpperCase())!
  );
}

function topEntry(codeOrLabel: string | null | undefined) {
  const raw = codeOrLabel?.trim() || DEFAULT_TOP_OPTION;
  const aliased = TOP_ALIASES[raw.toUpperCase()];
  const key = (aliased ?? raw).toUpperCase();
  return TOP_BY_CODE.get(key) ?? TOP_BY_CODE.get(DEFAULT_TOP_OPTION)!;
}

function interiorEntry(codeOrLabel: string | null | undefined) {
  const raw = codeOrLabel?.trim() || DEFAULT_INTERIOR_OPTION;
  const upper = raw.toUpperCase();
  const aliased = INT_ALIASES[upper] ?? INT_ALIASES[raw];
  const key = (aliased ?? raw).toUpperCase();
  return INT_BY_CODE.get(key) ?? INT_BY_CODE.get(DEFAULT_INTERIOR_OPTION)!;
}

export function formatSpecLabel(opt: {
  code: string;
  en: string;
  zh: string;
  order?: string;
}): string {
  const head = opt.order ? `${opt.code}/${opt.order}` : opt.code;
  return `${head} · ${opt.en} · ${opt.zh}`;
}

export function resolveBodyPaintHex(
  paintName: string | null | undefined,
): string {
  return paintEntry(paintName).hex;
}

/** Exterior brochure finish bucket (for 3D PBR). */
export type BodyPaintFinish = "solid" | "metallic" | "special";

/** Three.js-ish car-paint PBR; not a lab measurement. */
export type BodyPaintPbr = { metalness: number; roughness: number };

export function resolveBodyPaintFinish(
  paintName: string | null | undefined,
): BodyPaintFinish {
  const g = paintEntry(paintName).group;
  if (g === "metallic" || g === "special") return g;
  return "solid";
}

/**
 * Map brochure finish → viewer metalness/roughness.
 * Special: *Metallic* names share flake; Carmine-like specials stay near-solid.
 */
export function resolveBodyPaintPbr(
  paintName: string | null | undefined,
): BodyPaintPbr {
  const p = paintEntry(paintName);
  const finish = resolveBodyPaintFinish(paintName);
  if (finish === "metallic") return { metalness: 0.68, roughness: 0.28 };
  if (finish === "special") {
    if (/metallic/i.test(p.en)) return { metalness: 0.62, roughness: 0.3 };
    return { metalness: 0.12, roughness: 0.4 };
  }
  return { metalness: 0.08, roughness: 0.42 };
}

export function resolveSoftTopHex(top: string | null | undefined): string {
  return topEntry(top).hex;
}

/** Canonical soft-top code (migrates prior BLUE/6V/RED). */
export function resolveTopCode(top: string | null | undefined): TopOption {
  return topEntry(top).code as TopOption;
}

export function resolveInteriorHex(
  interior: string | null | undefined,
): string {
  return interiorEntry(interior).hex;
}

/** Canonical interior code (migrates prior brochure codes). */
export function resolveInteriorCode(
  interior: string | null | undefined,
): InteriorOption {
  return interiorEntry(interior).code as InteriorOption;
}

export function resolvePaintOemCode(
  paintName: string | null | undefined,
): string | null {
  if (!paintName?.trim()) return null;
  const p = paintEntry(paintName);
  return ("oem" in p && p.oem ? p.oem : null) ?? p.code;
}

export function resolvePaintCode(
  paintName: string | null | undefined,
): string {
  return paintEntry(paintName).code;
}
