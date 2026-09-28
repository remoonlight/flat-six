/**
 * Deterministic 981 public-candidate evidence index.
 * Usage:
 *   node scripts/build-981-evidence-index.mjs
 *   node scripts/build-981-evidence-index.mjs --check
 *   node --test scripts/build-981-evidence-index.mjs
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

const PATHS = {
  signals: "docs/research/can-data/internal/signals.json",
  internalSources: "docs/research/can-data/internal/sources.json",
  definitions: "docs/research/can-data/diagnostic/definitions.json",
  diagnosticSources: "docs/research/can-data/diagnostic/sources.json",
  captures: "docs/research/can-data/comfort/captures.json",
  comfortSources: "docs/research/can-data/comfort/sources.json",
  codingGuide: "data/seed/coding-guide/981.json",
  codingReadme: "data/seed/coding-guide/README.md",
  x431Menu: "data/seed/x431/981-2014-coding-menu.json",
  observed: "data/seed/diagnostics/catalog.v1.json",
  catalogOut: "data/seed/diagnostic-reference-981/catalog.json",
  manifestOut: "data/seed/diagnostic-reference-981/manifest.json",
};

const VBOX_981 = new Set(["vbox-ref-boxster-981", "vbox-ref-cayman-981"]);
const PROFILE_DME = "porsche-981-2014-dme";
const PROFILE_GW = "porsche-981-2014-gateway";
const REQUIRED_OBSERVED_PROFILES = [PROFILE_DME, PROFILE_GW];

function posix(p) {
  return p.split(path.sep).join("/");
}

function readJson(rel) {
  return JSON.parse(fs.readFileSync(path.join(ROOT, rel), "utf8"));
}

function sha256File(abs) {
  return createHash("sha256").update(fs.readFileSync(abs)).digest("hex");
}

function sha256Text(s) {
  return createHash("sha256").update(s).digest("hex");
}

function slug(s) {
  return String(s)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function modelsOf(rec) {
  return rec.models || rec.modelScope || [];
}

function recordHas981(rec) {
  return modelsOf(rec).includes("981");
}

function sourceHas981OnlyInherited(source, rec) {
  return (source?.models || []).includes("981") && !recordHas981(rec);
}

function sanitize(obj) {
  if (obj == null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map(sanitize);
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = k.toLowerCase();
    if (key === "vin" || key === "fullvin" || key === "serial") {
      out[k] = null;
      continue;
    }
    if (typeof v === "string" && /^WP0[A-Z0-9]{14}$/i.test(v.trim())) {
      out[k] = null;
      continue;
    }
    out[k] = sanitize(v);
  }
  return out;
}

function dump(obj) {
  return `${JSON.stringify(obj, null, 2)}\n`;
}

function hexNorm(s) {
  return String(s || "")
    .replace(/[^0-9A-Fa-f]/g, "")
    .toUpperCase();
}

function noneRelationship() {
  return {
    type: "none",
    note: "No observed ECU association.",
    matchedObservedOperationIds: [],
  };
}

function baseEntry(partial) {
  return {
    executionEnabled: false,
    decoderReady: false,
    relatedObservedProfileIds: [],
    observedRelationship: noneRelationship(),
    ...partial,
  };
}

function ecuAssociationProfileIds(def) {
  const ids = [];
  const ecu = String(def.ecu || "").toLowerCase();
  const req = String(def.requestAddress || "").toUpperCase();
  const res = String(def.responseAddress || "").toUpperCase();
  if (!recordHas981(def)) return ids;
  if (req === "7E0" && res === "7E8" && /dme|engine/.test(ecu)) ids.push(PROFILE_DME);
  if (req === "710" && res === "77A" && /gateway|zen|mod-?710|mod 0x710/.test(ecu)) {
    ids.push(PROFILE_GW);
  }
  return ids;
}

function observedLink(def, observed) {
  const associationIds = ecuAssociationProfileIds(def);
  const pidRaw = String(def.pidDidLocalId || "").trim();
  const reqHex = !pidRaw || /^(none|see-locator)$/i.test(pidRaw) ? "" : hexNorm(pidRaw);
  const matches = [];
  if (reqHex && observed?.operations) {
    const req = String(def.requestAddress || "").toUpperCase();
    const res = String(def.responseAddress || "").toUpperCase();
    for (const op of observed.operations) {
      if (op.tx === req && op.rx === res && op.requestHex === reqHex) {
        matches.push(`${op.profileId}/${op.operationId}`);
      }
    }
  }
  if (matches.length) {
    return {
      relatedObservedProfileIds: [...new Set(matches.map((m) => m.split("/")[0]))].sort(),
      observedRelationship: {
        type: "corroborated-operation",
        note: "Exact requestHex and address pair match an observed catalog operation. Not a send authorization.",
        matchedObservedOperationIds: [...new Set(matches)].sort(),
      },
    };
  }
  if (associationIds.length) {
    return {
      relatedObservedProfileIds: associationIds,
      observedRelationship: {
        type: "ecu-research-association",
        note: "Same diagnostic address pair and ECU label as an observed profile. Not same-service corroboration. Generic Mode 03/07 is not a matched transaction to captured KWP 18.",
        matchedObservedOperationIds: [],
      },
    };
  }
  return {
    relatedObservedProfileIds: [],
    observedRelationship: noneRelationship(),
  };
}

function loadObservedCatalog(root) {
  const rel = PATHS.observed;
  const abs = path.join(root, rel);
  const result = {
    path: rel,
    present: fs.existsSync(abs),
    schemaVersion: null,
    profileIds: [],
    operations: [],
    validated: false,
    validationErrors: [],
  };
  if (!result.present) {
    result.validationErrors.push("observed-catalog-missing");
    return result;
  }
  const obs = JSON.parse(fs.readFileSync(abs, "utf8"));
  result.schemaVersion = obs.schemaVersion ?? null;
  if (obs.schemaVersion !== 1) result.validationErrors.push("unexpected-schemaVersion");
  const profiles = obs.profiles || [];
  result.profileIds = profiles.map((p) => p.id);
  for (const need of REQUIRED_OBSERVED_PROFILES) {
    if (!result.profileIds.includes(need)) result.validationErrors.push(`missing-profile:${need}`);
  }
  for (const p of profiles) {
    for (const op of p.operations || []) {
      result.operations.push({
        profileId: p.id,
        operationId: op.id,
        tx: String(p.txId || "").toUpperCase(),
        rx: String(p.rxId || "").toUpperCase(),
        requestHex: hexNorm(op.requestHex),
      });
    }
  }
  result.validated = result.validationErrors.length === 0;
  return result;
}

function applicabilityClass(def) {
  const cat = def.definitionCategory;
  const ev = def.evidenceStatus || "";
  if (cat === "standard-index" || ev.includes("standard-pid") || ev.includes("standard-read-service")) {
    return "standard-if-supported";
  }
  if (cat === "channel-name-only" || ev.includes("channel-name")) return "channel-name-only";
  return "manufacturer-unverified";
}

function kindForDefinition(def) {
  if (def.definitionCategory === "channel-name-only") return "measurement-channel-reference";
  return "diagnostic-read-definition";
}

function verifyRawFiles(source, inputHashes, root = ROOT) {
  const files = source.rawFiles || [];
  if (files.length === 0) {
    inputHashes.rawArtifacts.push({
      sourceId: source.id,
      path: null,
      expectedSha256: null,
      present: false,
      absentRecorded: true,
      reason: "no-rawFiles-in-source-record",
    });
    return;
  }
  for (const f of files) {
    if (!f.path || !f.sha256) {
      inputHashes.rawArtifacts.push({
        sourceId: source.id,
        path: f.path ? posix(f.path) : null,
        expectedSha256: f.sha256 || null,
        present: false,
        absentRecorded: true,
        reason: "rawFiles-entry-missing-path-or-hash",
      });
      continue;
    }
    const abs = path.join(root, f.path);
    const rec = {
      sourceId: source.id,
      path: posix(f.path),
      expectedSha256: f.sha256.toLowerCase(),
      present: fs.existsSync(abs),
    };
    if (rec.present) {
      rec.actualSha256 = sha256File(abs).toLowerCase();
      rec.match = rec.actualSha256 === rec.expectedSha256;
    } else {
      rec.absentRecorded = true;
    }
    inputHashes.rawArtifacts.push(rec);
  }
}

function sourceSlice(src) {
  return {
    id: src.id,
    title: src.title,
    url: src.url || null,
    models: src.models || [],
    network: src.network || null,
    licenseStatus: src.licenseStatus || null,
    evidenceStatus: src.evidenceStatus || null,
    artifactKind: src.artifactKind || null,
    rawFiles: (src.rawFiles || []).map((f) => ({
      path: f.path || null,
      sha256: f.sha256 || null,
      bytes: f.bytes ?? null,
      url: f.url || null,
    })),
    notes: src.notes || null,
  };
}

export function buildCatalog(root = ROOT) {
  const exclusions = [];
  const unsupported = [];
  const conflicts = [];
  const entries = [];
  const sourceMap = new Map();
  const inputHashes = { indexes: {}, rawArtifacts: [] };
  const observed = loadObservedCatalog(root);

  const rels = [
    PATHS.signals,
    PATHS.internalSources,
    PATHS.definitions,
    PATHS.diagnosticSources,
    PATHS.captures,
    PATHS.comfortSources,
  ];
  const loaded = {};
  for (const rel of rels) {
    const abs = path.join(root, rel);
    inputHashes.indexes[rel] = sha256File(abs);
    loaded[rel] = JSON.parse(fs.readFileSync(abs, "utf8"));
  }

  const signals = loaded[PATHS.signals].signals;
  const internalSources = loaded[PATHS.internalSources].sources;
  const definitions = loaded[PATHS.definitions].definitions;
  const diagnosticSources = loaded[PATHS.diagnosticSources].sources;
  const captures = loaded[PATHS.captures].captures;
  const comfortSources = loaded[PATHS.comfortSources].sources;

  const srcById = new Map(
    [...internalSources, ...diagnosticSources, ...comfortSources].map((s) => [s.id, s]),
  );

  function rememberSource(id) {
    const s = srcById.get(id);
    if (s && !sourceMap.has(id)) {
      sourceMap.set(id, sourceSlice(s));
      verifyRawFiles(s, inputHashes, root);
    }
  }

  const vboxGroups = new Map();
  for (const sig of signals) {
    const src = srcById.get(sig.sourceId);
    if (sourceHas981OnlyInherited(src, sig)) {
      exclusions.push({
        reason: "record-models-exclude-981-do-not-inherit-source-scope",
        sourceId: sig.sourceId,
        pointer: sig.signalName,
      });
      continue;
    }
    if (!recordHas981(sig)) {
      exclusions.push({
        reason: "not-explicit-981",
        sourceId: sig.sourceId,
        pointer: sig.signalName || sig.canIdHex,
        models: modelsOf(sig),
      });
      continue;
    }
    if (VBOX_981.has(sig.sourceId)) {
      const key = sig.rawRow || `${sig.signalName}|${sig.canIdInt}|${sig.startBit}`;
      const g = vboxGroups.get(key) || [];
      g.push(sig);
      vboxGroups.set(key, g);
      continue;
    }
    entries.push(
      baseEntry({
        id: `broadcast-${sig.sourceId}-${slug(sig.signalName)}-${sig.canIdInt}-${sig.startBit}`,
        kind: "broadcast-signal",
        sourceId: sig.sourceId,
        sourceRecordPointer: {
          file: PATHS.signals,
          sourceId: sig.sourceId,
          signalName: sig.signalName,
          canIdInt: sig.canIdInt,
          startBit: sig.startBit,
        },
        models: [...modelsOf(sig)],
        yearQualifier: sig.yearQualifier || null,
        network: sig.network || null,
        originalDefinition: sanitize(sig),
        evidenceStatus: sig.evidenceStatus,
        applicabilityClass: "manufacturer-unverified",
        decoderReady: false,
        unit: sig.unit ?? null,
        factor: sig.factor ?? null,
        offset: sig.offset ?? null,
        byteOrder: sig.byteOrder ?? null,
        formula: sig.formula ?? null,
      }),
    );
    rememberSource(sig.sourceId);
  }

  const vboxFileHash = new Set();
  for (const id of VBOX_981) {
    const s = srcById.get(id);
    for (const f of s?.rawFiles || []) if (f.sha256) vboxFileHash.add(f.sha256.toLowerCase());
  }
  assert.equal(vboxFileHash.size, 1, "981 Boxster/Cayman REF must share one raw hash");

  for (const [rawRow, group] of vboxGroups) {
    const sourceIds = [...new Set(group.map((s) => s.sourceId))].sort();
    const sig = group[0];
    const motorola = group.some((s) => s.motorolaStartBitUnresolved || s.vectorDbcDecodeReady === false);
    entries.push(
      baseEntry({
        id: `broadcast-vbox-981-${slug(sig.signalName)}`,
        kind: "broadcast-signal",
        sourceId: sourceIds[0],
        sourceIds,
        sourceRecordPointer: {
          file: PATHS.signals,
          aliasSourceIds: sourceIds,
          rawRow,
          rawFileSha256: [...vboxFileHash][0],
        },
        models: ["981"],
        yearQualifier: "2012-2016 (VBOX REF filename)",
        network: "internal-drive",
        originalDefinition: sanitize(sig),
        evidenceStatus: sig.evidenceStatus,
        applicabilityClass: "manufacturer-unverified",
        decoderReady: false,
        motorolaStartBitUnresolved: motorola,
        unit: sig.unit ?? null,
        factor: sig.factor ?? null,
        offset: sig.offset ?? null,
        byteOrder: sig.byteOrder ?? null,
        formula: null,
        aliasDedup: {
          identicalRawHashAndRow: true,
          droppedDuplicateCount: group.length - 1,
        },
      }),
    );
    for (const id of sourceIds) rememberSource(id);
  }

  const vboxThrottle = signals.find(
    (s) => s.sourceId === "vbox-ref-boxster-981" && s.signalName === "Throttle_Position" && s.canIdInt === 261,
  );
  const pkThrottle = signals.find(
    (s) => s.sourceId === "planetkris-718-dbc" && s.signalName === "Throttle_Position" && s.canIdInt === 261,
  );
  const r991Throttle = signals.find(
    (s) => s.sourceId === "vbox-ref-911-991.1" && s.signalName === "Throttle_Position" && s.canIdInt === 261,
  );
  conflicts.push({
    id: "conflict-throttle-can-261",
    topic: "Throttle_Position on CAN 261",
    resolution: "unresolved-do-not-merge-718-or-991-into-981",
    candidates: [
      {
        sourceId: "vbox-ref-boxster-981",
        models: ["981"],
        startBit: vboxThrottle?.startBit ?? null,
        byteOrder: vboxThrottle?.byteOrder ?? null,
        factor: vboxThrottle?.factor ?? null,
      },
      {
        sourceId: "planetkris-718-dbc",
        models: ["982"],
        startBit: pkThrottle?.startBit ?? null,
        byteOrder: pkThrottle?.byteOrder ?? null,
        factor: pkThrottle?.factor ?? null,
      },
      {
        sourceId: "vbox-ref-911-991.1",
        models: ["991.1"],
        startBit: r991Throttle?.startBit ?? null,
        byteOrder: r991Throttle?.byteOrder ?? null,
        factor: r991Throttle?.factor ?? null,
      },
    ],
  });
  rememberSource("planetkris-718-dbc");
  rememberSource("vbox-ref-911-991.1");

  for (const src of internalSources) {
    if (!recordHas981(src)) continue;
    if (src.artifactKind === "racelogic-ref") continue;
    if (src.evidenceStatus === "channel-names-only" || String(src.artifactKind || "").includes("channel")) {
      entries.push(
        baseEntry({
          id: `channel-ref-${src.id}`,
          kind: "measurement-channel-reference",
          sourceId: src.id,
          sourceRecordPointer: { file: PATHS.internalSources, sourceId: src.id },
          models: [...modelsOf(src)],
          yearQualifier: src.notes || null,
          network: src.network || null,
          originalDefinition: sanitize({
            id: src.id,
            title: src.title,
            artifactKind: src.artifactKind,
            notes: src.notes,
            evidenceStatus: src.evidenceStatus,
          }),
          evidenceStatus: src.evidenceStatus,
          applicabilityClass: "channel-name-only",
          unit: null,
          formula: null,
        }),
      );
      rememberSource(src.id);
    }
  }

  for (const def of definitions) {
    if (!recordHas981(def)) {
      exclusions.push({
        reason: "not-explicit-981",
        sourceId: def.sourceId,
        pointer: def.id,
        models: modelsOf(def),
      });
      continue;
    }
    const decoder = def.decoder ?? null;
    entries.push(
      baseEntry({
        id: `diag-${def.id}`,
        kind: kindForDefinition(def),
        sourceId: def.sourceId,
        sourceRecordPointer: { file: PATHS.definitions, id: def.id },
        models: [...modelsOf(def)],
        yearQualifier: def.yearQualifier || null,
        network: "diagnostic-can",
        originalDefinition: sanitize(def),
        evidenceStatus: def.evidenceStatus,
        applicabilityClass: applicabilityClass(def),
        ...observedLink(def, observed),
        unit: def.unit ?? null,
        formula: decoder,
        decoder: decoder,
        requestAddress: def.requestAddress ?? null,
        responseAddress: def.responseAddress ?? null,
      }),
    );
    rememberSource(def.sourceId);
  }

  for (const src of diagnosticSources) {
    if (!recordHas981(src)) continue;
    if (src.evidenceStatus === "empty-commands-array") {
      unsupported.push({
        id: `unsupported-${src.id}`,
        kind: "diagnostic-read-definition",
        reason: "empty-commands-array",
        sourceId: src.id,
        models: modelsOf(src),
      });
      rememberSource(src.id);
    }
  }

  let comfort981Raw = 0;
  for (const cap of captures) {
    const stated = cap.availableMetadata?.statedModel || "";
    const src = srcById.get(cap.sourceId);
    if (/981/.test(stated) || recordHas981(cap) || recordHas981(src || {})) {
      comfort981Raw += 1;
      entries.push(
        baseEntry({
          id: `capture-${slug(cap.path || cap.sourceId)}`,
          kind: "capture-reference",
          sourceId: cap.sourceId,
          sourceRecordPointer: { file: PATHS.captures, path: cap.path },
          models: ["981"],
          yearQualifier: stated || null,
          network: "comfort-can",
          originalDefinition: sanitize({
            path: cap.path,
            format: cap.format,
            counts: cap.counts,
            evidenceLabels: cap.evidenceLabels,
          }),
          evidenceStatus: (cap.evidenceLabels || []).join(";"),
        }),
      );
    } else {
      exclusions.push({
        reason: "comfort-capture-not-981-do-not-relabel",
        sourceId: cap.sourceId,
        pointer: cap.path,
        statedModel: stated,
      });
    }
  }
  if (comfort981Raw === 0) {
    unsupported.push({
      id: "unsupported-comfort-981-no-raw-capture",
      kind: "capture-reference",
      reason: "no-981-comfort-raw-capture; 997.1 captures retained in research catalog only",
      sourceId: "comfort-981",
      sourceRecordPointer: { file: PATHS.comfortSources, note: "no-981-source-row" },
    });
    if (!sourceMap.has("comfort-981")) {
      const gap = {
        id: "comfort-981",
        title: "981 comfort CAN — no raw capture in this catalog",
        url: null,
        models: ["981"],
        network: "comfort-can",
        licenseStatus: null,
        evidenceStatus: "explicit-gap",
        artifactKind: "missing-raw-capture",
        rawFiles: [],
        notes: "997.1 captures are not relabeled as 981.",
      };
      sourceMap.set("comfort-981", gap);
      verifyRawFiles(gap, inputHashes, root);
    }
  }

  const codingPath = path.join(root, PATHS.codingGuide);
  inputHashes.indexes[PATHS.codingReadme] = sha256File(path.join(root, PATHS.codingReadme));
  if (fs.existsSync(codingPath)) {
    inputHashes.indexes[PATHS.codingGuide] = sha256File(codingPath);
    const guide = JSON.parse(fs.readFileSync(codingPath, "utf8"));
    const items = Array.isArray(guide) ? guide : guide.items || guide.ITEMS || [];
    for (const item of items) {
      const id = item.id ?? item.sourceId ?? item.name;
      entries.push(
        baseEntry({
          id: `coding-guide-${slug(id)}`,
          kind: "coding-menu-reference",
          sourceId: "stormeye818-coding-guide",
          sourceRecordPointer: { file: PATHS.codingGuide, id },
          models: ["981"],
          yearQualifier: item.compatStatus || item.compat || null,
          network: null,
          originalDefinition: sanitize(item),
          evidenceStatus: "community-reference-unverified-on-vehicle",
          applicabilityClass: "manufacturer-unverified",
          formula: null,
          payloads: null,
          compat: item.compat ?? null,
          hardwareConditions: item.hardware || item.notes || null,
        }),
      );
    }
    sourceMap.set("stormeye818-coding-guide", {
      id: "stormeye818-coding-guide",
      title: "StormEye818 981 coding guide extract",
      url: "https://github.com/StormEye818/porsche-coding-guide-tool",
      models: ["981"],
      licenseStatus: "MIT",
      rawFiles: [{ path: PATHS.codingGuide, sha256: inputHashes.indexes[PATHS.codingGuide], bytes: null, url: null }],
    });
  } else {
    unsupported.push({
      id: "unsupported-coding-guide-981-json-missing",
      kind: "coding-menu-reference",
      reason: "README names data/seed/coding-guide/981.json; file not present this run",
      sourceId: "stormeye818-coding-guide",
      sourceRecordPointer: { file: PATHS.codingReadme },
    });
    sourceMap.set("stormeye818-coding-guide", {
      id: "stormeye818-coding-guide",
      title: "StormEye818 981 coding guide extract",
      url: "https://github.com/StormEye818/porsche-coding-guide-tool",
      models: ["981"],
      licenseStatus: "MIT",
      rawFiles: [{ path: PATHS.codingGuide, sha256: null, bytes: null, url: null }],
      notes: "981.json named in README; absent this run.",
    });
    verifyRawFiles(sourceMap.get("stormeye818-coding-guide"), inputHashes, root);
  }

  const x431Abs = path.join(root, PATHS.x431Menu);
  if (fs.existsSync(x431Abs)) {
    inputHashes.indexes[PATHS.x431Menu] = sha256File(x431Abs);
    const menu = JSON.parse(fs.readFileSync(x431Abs, "utf8"));
    for (const sys of menu.systems || []) {
      for (const item of sys.items || []) {
        entries.push(
          baseEntry({
            id: `coding-x431-menu-${item.rowId}`,
            kind: "coding-menu-reference",
            sourceId: "x431-981-2014-coding-menu",
            sourceRecordPointer: { file: PATHS.x431Menu, rowId: item.rowId },
            models: ["981"],
            yearQualifier: String(menu.year || "2014"),
            network: null,
            originalDefinition: sanitize({
              system: sys.system,
              function: item.function,
              subFunction: item.subFunction,
              rowId: item.rowId,
              x431Path: item.playbook?.x431Path,
            }),
            evidenceStatus: "x431-menu-text-not-protocol",
            applicabilityClass: "manufacturer-unverified",
            formula: null,
            payloads: null,
          }),
        );
      }
    }
    sourceMap.set("x431-981-2014-coding-menu", {
      id: "x431-981-2014-coding-menu",
      title: "X431 2014 Boxster(981) coding menu archive",
      url: null,
      models: ["981"],
      licenseStatus: "local-archive",
      rawFiles: [{ path: PATHS.x431Menu, sha256: inputHashes.indexes[PATHS.x431Menu], bytes: null, url: null }],
    });
  }

  entries.sort((a, b) => a.id.localeCompare(b.id));
  const sources = [...sourceMap.values()].sort((a, b) => a.id.localeCompare(b.id));

  const byKind = {};
  const bySource = {};
  for (const e of entries) {
    byKind[e.kind] = (byKind[e.kind] || 0) + 1;
    bySource[e.sourceId] = (bySource[e.sourceId] || 0) + 1;
  }

  const catalog = {
    schemaVersion: 1,
    catalogId: "981-evidence-index",
    generatedBy: "scripts/build-981-evidence-index.mjs",
    note: "Public candidate catalog. executionEnabled=false for every entry. Not a vLinker send table.",
    inputHashes,
    entries,
    sources,
    coverage: {
      byKind,
      bySource,
      entryCount: entries.length,
      vbox981DedupedSignals: [...vboxGroups].length,
      vbox981RawRowsBeforeDedup: [...vboxGroups.values()].reduce((n, g) => n + g.length, 0),
      observedRelationship: {
        corroboratedOperation: entries.filter((e) => e.observedRelationship?.type === "corroborated-operation").length,
        ecuResearchAssociation: entries.filter((e) => e.observedRelationship?.type === "ecu-research-association").length,
      },
    },
    conflicts,
    unsupported,
    exclusions: {
      count: exclusions.length,
      reasons: exclusions.reduce((acc, x) => {
        acc[x.reason] = (acc[x.reason] || 0) + 1;
        return acc;
      }, {}),
    },
  };

  const linkedIds = [...new Set(entries.flatMap((e) => e.relatedObservedProfileIds))];
  const unknownLinked = linkedIds.filter((id) => !observed.profileIds.includes(id));
  if (unknownLinked.length) observed.validationErrors.push(`unknown-linked-profile:${unknownLinked.join(",")}`);
  observed.validated = observed.present && observed.validationErrors.length === 0;

  const manifest = {
    schemaVersion: 1,
    publicCandidates: {
      path: PATHS.catalogOut,
      executable: false,
      status: "built",
    },
    observedDefinitionsPath: PATHS.observed,
    observedDefinitions: {
      path: PATHS.observed,
      status: observed.present ? "present" : "missing",
      validated: observed.validated,
      schemaVersion: observed.schemaVersion,
      profileIdsChecked: REQUIRED_OBSERVED_PROFILES,
      note: "Path/schema/profile identity only. Observed catalog content is not hashed into this index.",
    },
    observedLiveEvidence: {
      copied: false,
      note: "Run hashes stay in the observed catalog. Not copied while that file may still be edited.",
    },
    unresolvedIntegrationPath: observed.validated ? null : observed.validationErrors,
  };

  return { catalog, manifest, observed };
}

export function accept(built) {
  const { catalog, manifest, observed } = built;
  assert.ok(catalog.entries.length > 0);
  assert.ok(observed?.validated, (observed?.validationErrors || []).join(";"));
  assert.equal(PATHS.observed in catalog.inputHashes.indexes, false);
  for (const rec of catalog.inputHashes.rawArtifacts) {
    if (rec.present) {
      assert.equal(rec.match, true, `raw hash mismatch ${rec.path}`);
    } else {
      assert.equal(rec.absentRecorded, true, `absent raw artifact not recorded ${rec.path || rec.sourceId}`);
    }
  }
  const sourceIds = new Set(catalog.sources.map((s) => s.id));
  for (const e of catalog.entries) {
    assert.equal(e.executionEnabled, false, e.id);
    assert.equal(e.decoderReady, false, e.id);
    assert.ok(e.id && e.kind && e.sourceId);
    assert.ok(sourceIds.has(e.sourceId), `missing source ${e.sourceId} for ${e.id}`);
    assert.ok(e.sourceRecordPointer);
    assert.ok(e.originalDefinition);
    assert.ok(Array.isArray(e.models) && e.models.includes("981"));
    for (const pid of e.relatedObservedProfileIds) {
      assert.ok(observed.profileIds.includes(pid), `unknown related profile ${pid} on ${e.id}`);
    }
    if (Object.prototype.hasOwnProperty.call(e, "formula") && e.kind === "broadcast-signal") {
      if (e.sourceIds?.some((id) => VBOX_981.has(id)) || VBOX_981.has(e.sourceId)) {
        assert.equal(e.formula, null);
      }
    }
  }
  for (const u of catalog.unsupported) {
    assert.ok(u.sourceId);
    assert.ok(sourceIds.has(u.sourceId), `unsupported missing source ${u.sourceId}`);
  }
  const kinds = new Set(catalog.entries.map((e) => e.kind));
  for (const k of [
    "diagnostic-read-definition",
    "broadcast-signal",
    "measurement-channel-reference",
    "coding-menu-reference",
  ]) {
    assert.ok(kinds.has(k), `missing kind ${k}`);
  }
  assert.equal(catalog.entries.some((e) => e.kind === "capture-reference"), false);
  assert.ok(catalog.unsupported.some((u) => u.id === "unsupported-comfort-981-no-raw-capture"));

  const vbox = catalog.entries.filter((e) => e.id.startsWith("broadcast-vbox-981-"));
  assert.equal(vbox.length, 17);
  assert.equal(catalog.coverage.vbox981RawRowsBeforeDedup, 34);
  for (const e of vbox) {
    assert.deepEqual(e.sourceIds, ["vbox-ref-boxster-981", "vbox-ref-cayman-981"]);
    assert.equal(e.decoderReady, false);
  }

  const excludedModels = catalog.exclusions.reasons["not-explicit-981"];
  assert.ok(excludedModels > 0);

  for (const e of catalog.entries) {
    const models = e.models;
    assert.ok(!models.every((m) => m !== "981"));
    const onlyOther = models.length && models.every((m) => /^(987|997|991|982)/.test(m));
    assert.equal(onlyOther, false, e.id);
  }

  const std = catalog.entries.filter((e) => e.applicabilityClass === "standard-if-supported");
  const mfr = catalog.entries.filter((e) => e.applicabilityClass === "manufacturer-unverified");
  assert.ok(std.length > 0);
  assert.ok(mfr.length > 0);
  assert.ok(!catalog.entries.some((e) => e.applicabilityClass === "verified981"));

  const throttleConflict = catalog.conflicts.find((c) => c.id === "conflict-throttle-can-261");
  assert.ok(throttleConflict);
  assert.equal(throttleConflict.resolution, "unresolved-do-not-merge-718-or-991-into-981");
  assert.equal(
    catalog.entries.filter((e) => e.sourceId === "planetkris-718-dbc").length,
    0,
  );

  const dme = catalog.entries.filter((e) => e.relatedObservedProfileIds.includes(PROFILE_DME));
  const gw = catalog.entries.filter((e) => e.relatedObservedProfileIds.includes(PROFILE_GW));
  assert.ok(dme.length >= 1);
  assert.ok(gw.length >= 1);
  for (const e of catalog.entries) {
    if (e.relatedObservedProfileIds.includes(PROFILE_DME)) {
      assert.equal(e.originalDefinition.requestAddress, "7E0");
      assert.match(String(e.originalDefinition.ecu), /DME|Engine/i);
    }
    if (e.relatedObservedProfileIds.includes(PROFILE_GW)) {
      assert.equal(e.originalDefinition.requestAddress, "710");
    }
  }
  const tpms = catalog.entries.find((e) => e.id.includes("tpms"));
  if (tpms) assert.equal(tpms.relatedObservedProfileIds.length, 0);

  const kwpDtc = catalog.entries.find((e) => e.id === "diag-dmitry-981-dme-kwp-read-dtc");
  assert.equal(kwpDtc.observedRelationship.type, "corroborated-operation");
  assert.ok(kwpDtc.observedRelationship.matchedObservedOperationIds.includes(`${PROFILE_DME}/dme-dtc`));

  const genericMode = catalog.entries.find((e) => e.id === "diag-dmitry-generic-mode03-07-dme");
  assert.equal(genericMode.observedRelationship.type, "ecu-research-association");
  assert.deepEqual(genericMode.observedRelationship.matchedObservedOperationIds, []);
  assert.ok(genericMode.relatedObservedProfileIds.includes(PROFILE_DME));

  const saeMode03 = catalog.entries.find((e) => e.id === "diag-sae-j1979-mode03-get-dtc");
  assert.notEqual(saeMode03.observedRelationship.type, "corroborated-operation");
  assert.equal(saeMode03.relatedObservedProfileIds.length, 0);

  const gwDtc = catalog.entries.find((e) => e.id === "diag-dmitry-981-module-mod-710-read-dtc");
  assert.equal(gwDtc.observedRelationship.type, "ecu-research-association");
  const gwF197 = catalog.entries.find((e) => e.id === "diag-dmitry-981-module-mod-710-read-f197");
  assert.equal(gwF197.observedRelationship.type, "ecu-research-association");

  assert.equal(manifest.observedDefinitionsPath, PATHS.observed);
  assert.equal(manifest.observedDefinitions.validated, true);
  assert.equal(manifest.publicCandidates.executable, false);

  for (const s of catalog.sources) {
    for (const f of s.rawFiles || []) {
      if (f.path) assert.ok(!f.path.includes("\\") && !/^[A-Za-z]:/.test(f.path), f.path);
    }
  }
  const vinHit = JSON.stringify(catalog.entries).match(/WP0[A-Z0-9]{14}/i);
  assert.equal(vinHit, null);
}

function writeOutputs(built) {
  const dir = path.join(ROOT, "data/seed/diagnostic-reference-981");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(ROOT, PATHS.catalogOut), dump(built.catalog));
  fs.writeFileSync(path.join(ROOT, PATHS.manifestOut), dump(built.manifest));
  fs.writeFileSync(
    path.join(dir, "README.md"),
    [
      "# 981 diagnostic reference (public candidates)",
      "",
      "Generated by `node scripts/build-981-evidence-index.mjs`.",
      "Every entry has `executionEnabled: false`. This is not a send catalog.",
      "",
      "- `catalog.json` — unified entries/sources/coverage/conflicts/unsupported",
      "- `manifest.json` — publicCandidates vs observed definitions path (`catalog.v1.json`)",
      "",
      "Rebuild check: `node scripts/build-981-evidence-index.mjs --check`",
      "",
    ].join("\n"),
  );
}

function checkOutputs(built) {
  for (const rel of [PATHS.catalogOut, PATHS.manifestOut]) {
    const abs = path.join(ROOT, rel);
    if (!fs.existsSync(abs)) throw new Error(`--check missing ${rel}`);
    const expected = rel.endsWith("manifest.json") ? dump(built.manifest) : dump(built.catalog);
    const actual = fs.readFileSync(abs, "utf8");
    if (actual !== expected) {
      throw new Error(`--check mismatch ${rel} (refusing overwrite)`);
    }
  }
}

const inTestRunner = Boolean(process.env.NODE_TEST_CONTEXT);
const asTest = process.execArgv.some((a) => a === "--test" || a.startsWith("--test="));
const wantTest = inTestRunner || asTest || process.argv.includes("--test");
const isMain = path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url);

if (isMain && !wantTest) {
  const check = process.argv.includes("--check");
  const built = buildCatalog(ROOT);
  accept(built);
  if (check) {
    checkOutputs(built);
    console.log("check ok", built.catalog.coverage);
  } else {
    writeOutputs(built);
    console.log("wrote", PATHS.catalogOut, PATHS.manifestOut, built.catalog.coverage);
  }
}

if (wantTest) {
  const { default: test } = await import("node:test");
  test("981 evidence index acceptance", () => {
    accept(buildCatalog(ROOT));
  });
}
