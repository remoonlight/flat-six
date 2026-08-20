/**
 * P1–P3 smoke: catalog prices, coding snapshot for 外部放大器/设码, locator hotspot parts
 */
import { spawn } from "node:child_process";
import readline from "node:readline";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const dbPath = path.join(root, ".local", "p123-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

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

const parts = await call("parts:list");
if (parts.length < 10) throw new Error("P1 parts seed missing");
const oil = parts.find((p) => p.sku === "engine-oil");
await call("parts:updatePrices", {
  id: oil.id,
  oem_price: 120,
  aftermarket_price: 45,
  price_note: "本地汽配城",
  price_as_of: "2026-07-30",
});
const updated = (await call("parts:list")).find((p) => p.id === oil.id);
if (updated.oem_price !== 120 || updated.aftermarket_price !== 45) {
  throw new Error("P1 price update failed");
}
console.log("P1 PASS");

const menu = await call("coding:menu");
const amp = menu.systems.find((s) => s.system === "外部放大器");
const coding = amp.items.find((i) => i.function === "设码");
if (!coding) throw new Error("P2 missing 外部放大器/设码");
await call("vehicle:setMileage", 50000);
await call("coding:add", {
  system: "外部放大器",
  function_name: "设码",
  sub_function: null,
  before_value: "00",
  after_value: "01",
  note: "accept test",
  odometer_km: 50000,
  recorded_at: new Date().toISOString(),
});
const snaps = await call("coding:list");
if (!snaps.length) throw new Error("P2 snapshot missing");
console.log("P2 PASS");

const faults = await call("faults:list");
if (faults.length < 3) throw new Error("P3 faults missing");
const spots = await call("locator:hotspots");
const engineParts = parts.filter((p) => p.locator_hotspot === "engine-bay");
if (!spots.find((s) => s.id === "engine-bay") || engineParts.length < 1) {
  throw new Error("P3 locator missing");
}
console.log("P3 PASS");
console.log("P1-P3 ACCEPT PASS");
child.kill();
process.exit(0);
