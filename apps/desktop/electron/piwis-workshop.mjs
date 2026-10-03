import fs from "node:fs/promises";
import { buildWorkshopPreview, WORKSHOP_PROGRAMMING_ECUS } from "@porsche981/domain";

const text = (v) => typeof v === "string" && v.length > 0 && v.length <= 4096;
const list = (v, limit) => Array.isArray(v) && v.length <= limit;
const condition = (c, current = false) => c &&
  (current ? ["PTNR", "HWTNR", "SWVERSION"] : ["PRODUKTSCHLUESSEL", "AUSSTATTUNG", "MODELLJAHR"]).includes(c.field)
  && list(c.values, 20) && c.values.length > 0 && c.values.every((v) => text(v) || (!current && c.field === "PRODUKTSCHLUESSEL" && v === ""));
export function validateWorkshopFlashIndex(doc) {
  if (!doc || doc.schemaVersion !== 1 || !list(doc.sources, 20) || !doc.sources.length || !list(doc.rules, 2000)) return false;
  if (!doc.sources.every((s) => s && text(s.file) && /^[a-f0-9]{64}$/.test(s.sha256))) return false;
  const sources = new Set(doc.sources.map((s) => s.file));
  const ids = new Set();
  return doc.rules.every((r) => {
    if (!r || !text(r.id) || ids.has(r.id) || !["981", "982"].includes(r.generation) || !WORKSHOP_PROGRAMMING_ECUS.includes(r.ecu)
      || !text(r.description) || !sources.has(r.source) || !list(r.conditions, 20) || !r.conditions.length || !list(r.targets, 20) || !r.targets.length) return false;
    const kind = r.kind ?? "firmware";
    if (!["firmware", "dataset", "blocked"].includes(kind) || ![undefined, "product-key", "shared-platform", "description"].includes(r.familyEvidence)) return false;
    if (r.currentEcus !== undefined && (!list(r.currentEcus, 20) || !r.currentEcus.every((e) => e && text(e.logicalLink)
      && list(e.conditions, 20) && e.conditions.every((c) => condition(c, true))))) return false;
    ids.add(r.id);
    return r.conditions.every((c) => condition(c)) && r.targets.every((t) => {
      if (!t || (t.session !== null && !text(t.session))) return false;
      if (kind === "blocked") return t.softwarePartNumber === null && (t.logicalLink === null || text(t.logicalLink));
      if (kind === "dataset") return text(t.logicalLink) && t.softwarePartNumber === null && text(t.session);
      return text(t.logicalLink) && text(t.softwarePartNumber) && t.softwarePartNumber.toLowerCase() !== "noflash";
    });
  });
}

/** Fixed application-owned file; renderer cannot choose a path or a diagnostic payload. */
export async function readWorkshopFlashIndex(file) {
  try {
    const handle = await fs.open(file, "r");
    try {
      if ((await handle.stat()).size > 2 * 1024 * 1024) return { status: "invalid", index: null };
      const doc = JSON.parse(await handle.readFile("utf8"));
      if (!validateWorkshopFlashIndex(doc)) return { status: "invalid", index: null };
      // Return only known fields, never arbitrary imported content.
      return { status: "loaded", index: {
        schemaVersion: 1,
        sources: doc.sources.map(({ file, sha256 }) => ({ file, sha256 })),
        rules: doc.rules.map(({ id, generation, ecu, description, conditions, targets, source, currentEcus, kind, familyEvidence }) => ({
          id, generation, ecu, description, source,
          kind: kind ?? "firmware", familyEvidence: familyEvidence ?? "description",
          conditions: conditions.map(({ field, values }) => ({ field, values })),
          currentEcus: (currentEcus ?? []).map(({ logicalLink, conditions }) => ({ logicalLink,
            conditions: conditions.map(({ field, values }) => ({ field, values })) })),
          targets: targets.map(({ logicalLink, softwarePartNumber, session }) => ({ logicalLink, softwarePartNumber, session })),
        })),
      } };
    } finally { await handle.close(); }
  } catch (e) {
    return { status: e.code === "ENOENT" ? "missing" : "invalid", index: null };
  }
}

export async function prepareWorkshopExport(input, file) {
  // Validate canonical function/options before touching the optional local rule index.
  const { flashRuleId, ...base } = input ?? {};
  buildWorkshopPreview(base);
  let rule;
  let sources = [];
  if (flashRuleId !== undefined) {
    const loaded = await readWorkshopFlashIndex(file);
    if (loaded.status !== "loaded") throw new Error("workshop-index-unavailable");
    rule = loaded.index.rules.find((r) => r.id === flashRuleId);
    sources = loaded.index.sources;
  }
  return { ...buildWorkshopPreview({ ...base, ...(flashRuleId === undefined ? {} : { flashRuleId }) }, rule), sources };
}
