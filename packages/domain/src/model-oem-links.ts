/**
 * 车库 3D 模型块 ↔ OEM（sku）手动关联。
 * 一个模型只挂一个 OEM；多个模型可挂同一 OEM。落盘 `.local/model-oem-links.json`。
 */

export type ModelOemKind = "assembly" | "mesh" | "flow";

export type ModelOemLink = {
  /** assembly/flow: `${kind}:${ref}`；mesh: `mesh:${assemblyId}:${ref}` */
  id: string;
  kind: ModelOemKind;
  /** assembly id / mesh name / flow id */
  ref: string;
  /** mesh 所属装配（独立关联键的一部分）；assembly/flow 可空 */
  assemblyId?: string | null;
  /** 至多一个主 sku（数组兼容旧文件，读写时取首个） */
  skus: string[];
  note?: string | null;
};

export type ModelOemLinkFile = {
  version: number;
  note: string;
  updated_at: string | null;
  links: ModelOemLink[];
};

export function modelOemLinkId(
  kind: ModelOemKind,
  ref: string,
  assemblyId?: string | null,
): string {
  if (kind === "mesh") {
    const asm = String(assemblyId ?? "").trim() || "_";
    return `mesh:${asm}:${ref}`;
  }
  return `${kind}:${ref}`;
}

function sameMeshSlot(
  link: ModelOemLink,
  ref: string,
  assemblyId?: string | null,
): boolean {
  if (link.kind !== "mesh" || link.ref !== ref) return false;
  const want = String(assemblyId ?? "").trim();
  const have = String(link.assemblyId ?? "").trim();
  if (want && have) return want === have;
  if (!want && !have) return true;
  return false;
}

/** 旧 `mesh:${name}` → `mesh:${assemblyId}:${name}`；已是新键则原样。 */
export function migrateModelOemLinkIds(
  links: readonly ModelOemLink[],
): { links: ModelOemLink[]; changed: boolean } {
  let changed = false;
  const next = links.map((l) => {
    if (l.kind !== "mesh") return l;
    const id = modelOemLinkId("mesh", l.ref, l.assemblyId);
    if (l.id === id) return l;
    changed = true;
    return { ...l, id };
  });
  return { links: next, changed };
}

export function emptyModelOemLinkFile(): ModelOemLinkFile {
  return {
    version: 1,
    note: "一个模型 → 一个 OEM；多模型可共享同一 OEM。装配优先，mesh 可补",
    updated_at: null,
    links: [],
  };
}

export function findModelOemLink(
  links: readonly ModelOemLink[],
  kind: ModelOemKind,
  ref: string,
  assemblyId?: string | null,
): ModelOemLink | null {
  const id = modelOemLinkId(kind, ref, assemblyId);
  const exact = links.find((l) => l.id === id);
  if (exact) return exact;
  if (kind !== "mesh") {
    return links.find((l) => l.kind === kind && l.ref === ref) ?? null;
  }
  return links.find((l) => sameMeshSlot(l, ref, assemblyId)) ?? null;
}

/** 链接上的主 sku（只认第一个）。 */
export function primarySkuOfLink(link: {
  skus?: readonly string[] | null;
}): string | null {
  const s = link.skus?.[0]?.trim();
  return s || null;
}

/**
 * 解析模型项关联的单个 OEM sku。
 * mesh / flow / assembly 各自独立；mesh **不**回落到所属装配。
 */
export function resolveModelOemSkus(
  links: readonly ModelOemLink[],
  item: {
    kind: ModelOemKind;
    ref: string;
    assemblyId?: string | null;
  },
): string[] {
  const sku = resolveModelOemSku(links, item);
  return sku ? [sku] : [];
}

export function resolveModelOemSku(
  links: readonly ModelOemLink[],
  item: {
    kind: ModelOemKind;
    ref: string;
    assemblyId?: string | null;
  },
): string | null {
  const row = findModelOemLink(links, item.kind, item.ref, item.assemblyId);
  return row ? primarySkuOfLink(row) : null;
}

/** 反查：挂到该 sku 的所有模型块。 */
export function findModelsForSku(
  links: readonly ModelOemLink[],
  sku: string,
): Array<{ kind: ModelOemKind; ref: string; assemblyId?: string | null }> {
  const out: Array<{
    kind: ModelOemKind;
    ref: string;
    assemblyId?: string | null;
  }> = [];
  for (const link of links) {
    if (primarySkuOfLink(link) !== sku) continue;
    out.push({
      kind: link.kind,
      ref: link.ref,
      assemblyId: link.assemblyId,
    });
  }
  return out;
}

export type OemLabelPart = {
  sku: string;
  oem_number: string | null;
  name_zh: string;
  name_en?: string | null;
  petka_note?: string | null;
  pr_label?: string | null;
};

/** PETKA `PR:447,450` + `447=紧急备用轮胎; 450=…` → `447 紧急备用轮胎 · 450 …` */
export function formatPetkaPrLabel(
  modelCodes: string | null | undefined,
  modelMeaning: string | null | undefined,
): string | null {
  const meaning = String(modelMeaning ?? "").trim();
  const map = new Map<string, string>();
  if (meaning) {
    for (const chunk of meaning.split(/[;；]/)) {
      const m = chunk.trim().match(/^([A-Za-z0-9]+)\s*=\s*(.+)$/);
      if (m) map.set(m[1]!, m[2]!.trim());
    }
  }
  const codes = String(modelCodes ?? "")
    .trim()
    .replace(/^PR:\s*/i, "")
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const parts: string[] = [];
  if (codes.length) {
    for (const c of codes) {
      const expl = map.get(c);
      parts.push(expl ? `${c} ${expl}` : c);
    }
  } else if (map.size) {
    for (const [c, expl] of map) parts.push(`${c} ${expl}`);
  }
  return parts.length ? parts.join(" · ") : null;
}

function displayPartName(p: OemLabelPart): string | null {
  const zh = (p.name_zh ?? "").trim();
  if (
    zh &&
    /[\u4e00-\u9fff]/.test(zh) &&
    !isOemPlaceholderName(zh, p.oem_number)
  ) {
    return zh;
  }
  const en = p.name_en?.trim();
  if (en) return en;
  if (zh && !isOemPlaceholderName(zh, p.oem_number)) return zh;
  return null;
}

export type ModelChildCaption = {
  /** OEM 号；未关联时为 mesh 名 */
  title: string;
  /** 中文名（无则英文）· 备注 · PR；未关联为「未关联」 */
  detail: string;
};

/** 子模型列表：OEM + 中文名（无中文→英文）+ 备注 + PR 代号解释 */
export function formatModelChildCaption(
  meshName: string,
  skus: readonly string[],
  parts: readonly OemLabelPart[],
): ModelChildCaption {
  const sku = skus[0]?.trim();
  if (!sku) return { title: meshName, detail: "未关联" };
  const p = parts.find((x) => x.sku === sku);
  if (!p) return { title: meshName, detail: sku };
  const oem = p.oem_number?.trim() || sku;
  const name =
    (p.oem_number?.trim()
      ? resolveOemPartLabel(parts, p.oem_number)
      : null) ?? displayPartName(p);
  const segs: string[] = [];
  if (name) segs.push(name);
  const note = p.petka_note?.trim();
  if (note) segs.push(note);
  const pr = p.pr_label?.trim();
  if (pr) segs.push(pr.startsWith("PR") ? pr : `PR ${pr}`);
  return { title: oem, detail: segs.length ? segs.join(" · ") : "已关联" };
}

/** name_zh 是否只是 OEM 压缩号（bulk 缺描述时的占位）。 */
export function isOemPlaceholderName(
  nameZh: string | null | undefined,
  oemNumber: string | null | undefined,
): boolean {
  const name = (nameZh ?? "").trim();
  if (!name) return true;
  if (/[\u4e00-\u9fff]/.test(name)) return false;
  const oem = (oemNumber ?? "").trim();
  if (!oem) return /^[0-9A-Za-z.\s-]{8,}$/.test(name) && !/[a-z]{3,}/i.test(name);
  return (
    name.replace(/[.\s-]/g, "").toUpperCase() ===
    oem.replace(/[.\s-]/g, "").toUpperCase()
  );
}

/**
 * 同 OEM 多行时优先：含汉字名称 > 非 OEM 占位名 > name_en > 任意。
 */
export function resolveOemPartLabel(
  parts: readonly (OemLabelPart & { name_en?: string | null })[],
  oemNumber: string,
): string | null {
  const needle = oemNumber.replace(/[.\s-]/g, "").toUpperCase();
  if (!needle) return null;
  const hits = parts.filter((p) => {
    const oem = p.oem_number?.trim();
    if (!oem) return false;
    return oem.replace(/[.\s-]/g, "").toUpperCase() === needle;
  });
  if (hits.length === 0) return null;
  const withHan = hits.find((p) => /[\u4e00-\u9fff]/.test(p.name_zh ?? ""));
  if (withHan?.name_zh?.trim()) return withHan.name_zh.trim();
  const real = hits.find(
    (p) => !isOemPlaceholderName(p.name_zh, p.oem_number),
  );
  if (real?.name_zh?.trim()) return real.name_zh.trim();
  const en = hits.find((p) => p.name_en?.trim())?.name_en?.trim();
  if (en) return en;
  return null;
}

/** `OEM · 中文名`；无可用名称时 `OEM · （无中文名）` */
export function formatOemWithName(
  oemNumber: string,
  nameZh: string | null | undefined,
): string {
  const oem = oemNumber.trim();
  const name = (nameZh ?? "").trim();
  // 仅展示汉字名；英文 name_en / OEM 占位一律视为无中文名
  if (!name || !/[\u4e00-\u9fff]/.test(name) || isOemPlaceholderName(name, oem)) {
    return `${oem} · （无中文名）`;
  }
  return `${oem} · ${name}`;
}

/** 展示用：OEM 号 · 中文名；无 → 未关联 */
export function formatModelOemPointer(
  skus: readonly string[],
  parts: readonly OemLabelPart[],
): string {
  const sku = skus[0]?.trim();
  if (!sku) return "未关联";
  const p = parts.find((x) => x.sku === sku);
  if (!p) return sku;
  const oem = p.oem_number?.trim() || p.sku;
  const label =
    (p.oem_number?.trim()
      ? resolveOemPartLabel(parts, p.oem_number)
      : null) ??
    (!isOemPlaceholderName(p.name_zh, p.oem_number) && /[\u4e00-\u9fff]/.test(p.name_zh)
      ? p.name_zh.trim()
      : null);
  return formatOemWithName(oem, label);
}

export type OemLookupPart = OemLabelPart & {
  generation?: string | null;
};

export type OemSuggestHit = {
  sku: string;
  oem_number: string;
  /** 展示用名称：优先汉字；可能为 null → UI 显示（无中文名） */
  name_zh: string | null;
  /** `OEM · 名称` 一行 */
  label: string;
};

function partNameQuality(p: OemLookupPart): number {
  const name = p.name_zh ?? "";
  if (/[\u4e00-\u9fff]/.test(name)) return 0;
  if (!isOemPlaceholderName(name, p.oem_number)) return 1;
  const g = (p.generation ?? "").trim().toUpperCase();
  if (g === "981") return 2;
  if (!g) return 3;
  return 4;
}

/**
 * 输入未完成时联想 OEM：归一化前缀优先，其次包含；
 * 同号去重优先带中文名的行；最多 limit 条。
 * 单次 O(n)，不整表 sort、不反复全表扫名。
 */
export function suggestOemByInput(
  parts: readonly OemLookupPart[],
  input: string,
  limit = 10,
): OemSuggestHit[] {
  const needle = input.replace(/[.\s]/g, "").toUpperCase();
  if (!needle || limit <= 0) return [];

  type Row = { score: number; quality: number; hit: OemSuggestHit };
  const best = new Map<string, Row>();

  for (const p of parts) {
    const oem = p.oem_number?.trim();
    if (!oem) continue;
    const norm = oem.replace(/[.\s]/g, "").toUpperCase();
    let score = 0;
    if (norm.startsWith(needle)) score = 2;
    else if (norm.includes(needle)) score = 1;
    else continue;

    const quality = partNameQuality(p);
    const prev = best.get(norm);
    if (prev && prev.quality <= quality) continue;

    const zh = (p.name_zh ?? "").trim();
    const nameZh =
      (/[\u4e00-\u9fff]/.test(zh) ? zh : null) ??
      (zh && !isOemPlaceholderName(zh, oem) ? zh : null) ??
      p.name_en?.trim() ??
      null;
    best.set(norm, {
      score,
      quality,
      hit: {
        sku: p.sku,
        oem_number: oem,
        name_zh: nameZh,
        label: formatOemWithName(oem, nameZh),
      },
    });
  }

  return [...best.values()]
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.hit.oem_number.localeCompare(b.hit.oem_number, "en"),
    )
    .slice(0, limit)
    .map((r) => r.hit);
}

/**
 * 用用户填写的 OEM 号反查本机零件 sku。
 * 归一化后精确匹配；多命中时优先 generation=981。
 */
export function findSkuByOemNumber(
  parts: readonly OemLookupPart[],
  oemInput: string,
): string | null {
  const needle = oemInput.replace(/[.\s]/g, "").toUpperCase();
  if (!needle) return null;
  const hits = parts.filter((p) => {
    const oem = p.oem_number?.trim();
    if (!oem) return false;
    return oem.replace(/[.\s]/g, "").toUpperCase() === needle;
  });
  if (hits.length === 0) return null;
  const p981 = hits.find(
    (p) => (p.generation ?? "").trim().toUpperCase() === "981",
  );
  return (p981 ?? hits[0])!.sku;
}

/**
 * 点选模型时要展示的 sku：仅该项自身关联（装配不汇总子 mesh）。
 */
export function collectSkusForModelSelection(
  links: readonly ModelOemLink[],
  item: {
    kind: ModelOemKind;
    ref: string;
    assemblyId?: string | null;
  },
): string[] {
  return resolveModelOemSkus(links, item);
}
