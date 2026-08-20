/**
 * Node-side DB bridge — Electron main talks JSON-lines over stdio.
 * Uses node:sqlite (available in host Node 22+, not in Electron's Node).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import readline from "node:readline";
import { cmsHintForZone } from "./cms-assets.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");

const dbMod = await import(
  pathToFileURL(path.join(repoRoot, "packages/db/dist/index.js")).href
);
const { GarageDb } = dbMod;

const dbPath = process.env.PORSCHE981_DB;
if (!dbPath) {
  console.error("PORSCHE981_DB required");
  process.exit(1);
}

const garage = new GarageDb(dbPath);

function loadJson(rel) {
  return JSON.parse(
    fs.readFileSync(path.join(repoRoot, "data", "seed", rel), "utf8"),
  );
}

function mimeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  return "application/octet-stream";
}

function fileToDataUrl(absPath) {
  const buf = fs.readFileSync(absPath);
  const mime = mimeFor(absPath);
  const b64 = buf.toString("base64");
  return `data:${mime};base64,${b64}`;
}

function resolveZoneBackground(zone) {
  const shotHint =
    (zone.shotCandidates && zone.shotCandidates[0]) ||
    `petka/${zone.id}/shots/overview.png`;
  for (const rel of zone.shotCandidates || []) {
    const abs = path.join(repoRoot, "data", rel);
    if (fs.existsSync(abs)) {
      return {
        source: "petka-shot",
        shotHint,
        dataUrl: fileToDataUrl(abs),
      };
    }
  }
  const placeholderAbs = path.join(repoRoot, "data", zone.placeholderRel);
  if (!fs.existsSync(placeholderAbs)) {
    throw new Error(`locator_placeholder_missing:${zone.id}`);
  }
  return {
    source: "placeholder",
    shotHint,
    dataUrl: fileToDataUrl(placeholderAbs),
  };
}

function loadLocatorMap() {
  const raw = loadJson("locator/zones.json");
  const zones = (raw.zones || []).map((z) => {
    const bg = resolveZoneBackground(z);
    const cms = cmsHintForZone(z.id);
    return {
      id: z.id,
      label_zh: z.label_zh,
      status: z.status || "pending-3d",
      shotHint: bg.shotHint,
      note: z.note || null,
      background: {
        source: bg.source,
        dataUrl: bg.dataUrl,
      },
      cms,
      hotspots: (z.hotspots || []).map((h) => ({
        id: h.id,
        label: h.label,
        x: Number(h.x),
        y: Number(h.y),
      })),
    };
  });
  return { note: raw.note || "", zones };
}

function flattenHotspots(map) {
  const seen = new Map();
  for (const z of map.zones) {
    for (const h of z.hotspots) {
      if (!seen.has(h.id)) seen.set(h.id, h);
    }
  }
  return [...seen.values()];
}

try {
  const partsSeed = loadJson("parts/bootstrap.json");
  const faultsSeed = loadJson("faults/bootstrap.json");
  const dtcSeed = loadJson("dtc/bootstrap.json");
  garage.seedIfEmpty({
    parts: partsSeed.parts,
    faults: faultsSeed.faults,
    dtcs: dtcSeed.dtcs,
  });
  garage.seedDtcIfEmpty(dtcSeed.dtcs);
  garage.seedDtcMissing(dtcSeed.dtcs);
} catch (e) {
  console.error("seed failed", e);
}

const handlers = {
  "vehicle:get": () => garage.getVehicle(),
  "vehicle:setMileage": (km) => garage.setMileage(Number(km)),
  "vehicle:setAvgKmPerDay": (avg) =>
    garage.setAvgKmPerDay(avg == null || avg === "" ? null : Number(avg)),
  "vehicle:setVin": (vin) => garage.setVin(vin ? String(vin) : null),
  "vehicle:setSettings": (payload) =>
    garage.setVehicleSettings({
      paint_name: payload?.paint_name ?? null,
      paint_code: payload?.paint_code ?? null,
      interior: payload?.interior ?? null,
      top: payload?.top ?? null,
    }),
  "parts:list": () => garage.listParts(),
  "parts:updatePrices": (payload) =>
    garage.updatePartPrices(
      payload.id,
      payload.oem_price,
      payload.aftermarket_price,
      payload.price_note,
      payload.price_as_of,
      payload.aftermarket_quotes,
    ),
  "parts:interval": (partId) => garage.partIntervalStatus(Number(partId)),
  "service:list": () => garage.listServiceRecords(),
  "service:add": (input) => garage.addServiceRecord(input),
  "faults:list": () => garage.listFaults(),
  "faultLogs:list": () => garage.listFaultLogs(),
  "faultLogs:add": (input) => garage.addFaultLog(input),
  "faultLogs:close": (id) => garage.closeFaultLog(Number(id)),
  "dtc:search": (prefix) => garage.searchDtc(prefix ?? ""),
  "dtc:get": (code) => garage.getDtcByCode(code),
  "obdSessions:list": () => garage.listObdSessions(),
  "obdSessions:create": (input) => garage.createObdSession(input ?? {}),
  "obdDtcs:add": (input) => garage.addObdDtc(input),
  "obdDtcs:list": (sessionId) => garage.listObdDtcs(Number(sessionId)),
  "coding:menu": () => {
    try {
      return loadJson("x431/981-2014-coding-menu.json");
    } catch {
      return { systems: [], rowCount: 0 };
    }
  },
  "coding:list": () => garage.listCodingSnapshots(),
  "coding:add": (input) => garage.addCodingSnapshot(input),
  "locator:map": () => loadLocatorMap(),
  "locator:hotspots": () => flattenHotspots(loadLocatorMap()),
  "locator:systemsDraft": () => {
    try {
      return loadJson("locator/systems-draft.plan.json");
    } catch {
      return { version: 1, status: "missing", bridgeJumps: [], zones: [] };
    }
  },
  "fx:get": () => {
    try {
      return loadJson("fx.json");
    } catch {
      return null;
    }
  },
  ping: () => ({ ok: true }),
};

const rl = readline.createInterface({ input: process.stdin });

rl.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    process.stdout.write(
      JSON.stringify({ id: null, error: "invalid_json" }) + "\n",
    );
    return;
  }
  const { id, method, params } = msg;
  try {
    const fn = handlers[method];
    if (!fn) throw new Error(`unknown_method:${method}`);
    const result = fn(params);
    process.stdout.write(JSON.stringify({ id, result }) + "\n");
  } catch (e) {
    process.stdout.write(
      JSON.stringify({ id, error: e instanceof Error ? e.message : String(e) }) +
        "\n",
    );
  }
});

process.stdout.write(JSON.stringify({ id: 0, result: { ready: true } }) + "\n");
