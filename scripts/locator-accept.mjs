/**
 * Locator P-Loc-1 smoke: three zones, hotspots filter parts from garage.db
 */
import { spawn } from "node:child_process";
import readline from "node:readline";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const dbPath = path.join(root, ".local", "garage.db");

const child = spawn("node", ["apps/desktop/electron/db-bridge.mjs"], {
  cwd: root,
  env: { ...process.env, PORSCHE981_DB: dbPath },
  stdio: ["pipe", "pipe", "pipe"],
});

const rl = readline.createInterface({ input: child.stdout });
let id = 1;
function call(method, params) {
  return new Promise((resolve, reject) => {
    const my = id++;
    const onLine = (line) => {
      const msg = JSON.parse(line);
      if (msg.id === 0 || msg.id !== my) return;
      rl.off("line", onLine);
      if (msg.error) reject(new Error(msg.error));
      else resolve(msg.result);
    };
    rl.on("line", onLine);
    child.stdin.write(JSON.stringify({ id: my, method, params }) + "\n");
  });
}

await new Promise((resolve) => rl.once("line", resolve));

const map = await call("locator:map");
const parts = await call("parts:list");
const spots = await call("locator:hotspots");

const expectedZones = ["engine-bay", "brakes", "chassis"];
const gotZones = map.zones.map((z) => z.id);
for (const z of expectedZones) {
  if (!gotZones.includes(z)) throw new Error(`missing zone ${z}`);
}

for (const z of map.zones) {
  if (!z.background?.dataUrl?.startsWith("data:")) {
    throw new Error(`zone ${z.id} missing background dataUrl`);
  }
  if (!z.hotspots?.length) throw new Error(`zone ${z.id} has no hotspots`);
  if (!z.cms || typeof z.cms.present !== "boolean") {
    throw new Error(`zone ${z.id} missing cms hint`);
  }
  console.log(
    `zone ${z.id}: bg=${z.background.source} cms=${z.cms.present ? z.cms.rel : "absent"} hotspots=${z.hotspots.map((h) => h.id).join(",")}`,
  );
}

const checks = [
  ["engine-bay", "engine-bay", 1],
  ["brakes", "front-left", 1],
  ["brakes", "rear-left", 1],
  ["chassis", "underbody", 1],
];

for (const [zoneId, hotspotId, min] of checks) {
  const zone = map.zones.find((z) => z.id === zoneId);
  if (!zone.hotspots.find((h) => h.id === hotspotId)) {
    throw new Error(`${zoneId} missing hotspot ${hotspotId}`);
  }
  const hit = parts.filter((p) => p.locator_hotspot === hotspotId);
  if (hit.length < min) {
    throw new Error(
      `${hotspotId} expected >=${min} parts, got ${hit.length}`,
    );
  }
  console.log(
    `hotspot ${hotspotId}: ${hit.length} parts → ${hit.map((p) => p.sku).join(", ")}`,
  );
}

if (!spots.find((s) => s.id === "engine-bay")) {
  throw new Error("locator:hotspots missing engine-bay (compat)");
}

// R4: Locator part card — service history filtered by part_id (listService client filter)
const target =
  parts.find((p) => p.locator_hotspot) ?? parts[0];
if (!target) throw new Error("no parts for service-history check");

const marker = `locator-r4-${Date.now()}`;
const inserted = await call("service:add", {
  part_id: target.id,
  title: marker,
  replaced_at: "2026-07-31",
  odometer_km: 88888,
  brand: "Mann",
  cost: 123.45,
});
if (inserted.part_id !== target.id) {
  throw new Error(`service:add part_id mismatch: ${inserted.part_id}`);
}

const allSvc = await call("service:list");
const forPart = allSvc.filter((r) => r.part_id === target.id);
const hit = forPart.find((r) => r.id === inserted.id);
if (!hit) {
  throw new Error(`filter by part_id=${target.id} missed inserted id=${inserted.id}`);
}
const otherParts = parts.filter((p) => p.id !== target.id);
if (otherParts.length) {
  const leaked = allSvc
    .filter((r) => r.part_id === otherParts[0].id)
    .some((r) => r.id === inserted.id);
  if (leaked) throw new Error("service leaked into unrelated part filter");
}
console.log(
  `R4 service filter: part#${target.id} (${target.sku}) → ${forPart.length} record(s); inserted id=${hit.id} ${hit.replaced_at} ${hit.odometer_km}km ${hit.brand} ¥${hit.cost}`,
);

console.log("LOCATOR ACCEPT PASS");
child.kill();
process.exit(0);
