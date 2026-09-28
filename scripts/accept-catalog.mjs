/**
 * Catalog merge via db-bridge on an isolated temp fixture (no .local).
 * Copies explicit shipped data + built packages; junctions workspace pkgs.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";
import {
  assertPetkaEpcFixtures,
  isPetkaPriceVerified,
  parsePetkaEpcCsv,
} from "../packages/domain/dist/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const TRACKED_FILES = [
  "apps/desktop/electron/db-bridge.mjs",
  "apps/desktop/electron/cms-assets.mjs",
  "packages/db/package.json",
  "packages/domain/package.json",
  "data/seed/parts/bootstrap.json",
  "data/seed/parts/catalog/981.csv",
  "data/seed/parts/catalog/982.csv",
  "data/seed/parts/catalog/teile-oem-exact.json",
  "data/seed/parts/catalog/README.md",
  "data/seed/fx.json",
  "data/seed/faults/bootstrap.json",
  "data/seed/dtc/bootstrap.json",
  "data/seed/petka/plaintext-archive/PETKA_Porsche_981_EPC.csv",
  "data/seed/xray/model-oem-links.seed.json",
  "data/petka/engine-bay/parts.csv",
  "data/petka/brakes/parts.csv",
  "data/petka/chassis/parts.csv",
];

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function copyFileRel(fromRoot, toRoot, rel) {
  const src = path.join(fromRoot, rel);
  assert(fs.existsSync(src), `missing tracked file ${rel}`);
  const dest = path.join(toRoot, rel);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function copyDirRel(fromRoot, toRoot, rel) {
  const src = path.join(fromRoot, rel);
  assert(fs.existsSync(src), `missing dir ${rel}`);
  fs.cpSync(src, path.join(toRoot, rel), { recursive: true });
}

function linkPkg(fixture, name) {
  const nm = path.join(fixture, "node_modules", "@porsche981");
  fs.mkdirSync(nm, { recursive: true });
  const dest = path.join(nm, name);
  const target = path.join(fixture, "packages", name);
  try {
    fs.symlinkSync(target, dest, "junction");
  } catch {
    fs.cpSync(target, dest, { recursive: true });
  }
}

function materializeFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche981-catfix-"));
  for (const rel of TRACKED_FILES) copyFileRel(root, dir, rel);
  copyDirRel(root, dir, path.join("packages", "db", "dist"));
  copyDirRel(root, dir, path.join("packages", "domain", "dist"));
  linkPkg(dir, "db");
  linkPkg(dir, "domain");
  assert(!fs.existsSync(path.join(dir, ".local")), "fixture must not have .local");
  return dir;
}

function startBridge(fixture, dbPath) {
  const bridgeScript = path.join(fixture, "apps/desktop/electron/db-bridge.mjs");
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [bridgeScript], {
      cwd: fixture,
      env: { ...process.env, PORSCHE981_DB: dbPath },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let buf = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error("bridge ready timeout"));
    }, 120_000);
    const onExit = (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`bridge exited before ready: ${code}`));
    };
    const onData = (chunk) => {
      buf += String(chunk);
      if (!settled && buf.includes('"ready":true')) {
        settled = true;
        clearTimeout(timer);
        child.stdout.off("data", onData);
        child.off("exit", onExit);
        resolve({ child, rest: buf });
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", (c) => process.stderr.write(c));
    child.on("exit", onExit);
  });
}

function stopBridge(child) {
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        /* */
      }
      resolve();
    }, 4000);
    child.removeAllListeners("exit");
    child.once("exit", () => {
      clearTimeout(t);
      resolve();
    });
    child.kill();
  });
}

function rpcPartsList(child) {
  return new Promise((resolve, reject) => {
    let buf = "";
    const timer = setTimeout(() => reject(new Error("parts:list timeout")), 60_000);
    const onData = (chunk) => {
      buf += String(chunk);
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        let msg;
        try {
          msg = JSON.parse(line);
        } catch {
          continue;
        }
        if (msg.id === 1) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          if (msg.error) reject(new Error(String(msg.error)));
          else resolve(msg.result);
        }
      }
    };
    child.stdout.on("data", onData);
    child.stdin.write(
      JSON.stringify({ id: 1, method: "parts:list", params: null }) + "\n",
    );
  });
}

function uniqueLinkSkus(fixture) {
  const links = JSON.parse(
    fs.readFileSync(
      path.join(fixture, "data/seed/xray/model-oem-links.seed.json"),
      "utf8",
    ),
  );
  return [
    ...new Set(
      (links.links || [])
        .flatMap((l) => l.skus || [])
        .filter((s) => typeof s === "string" && s.trim()),
    ),
  ];
}

function checkCatalogShape(dbPath, fixture) {
  const db = new GarageDb(dbPath);
  const parts = db.listParts();
  const bySku = new Map(parts.map((p) => [p.sku, p]));
  const oil = bySku.get("oil-filter");
  const oilBulk = bySku.get("981-9A110722400");
  const wheel = bySku.get("981-99134115806");
  const n981 = parts.filter((p) => p.generation === "981").length;
  const n982 = parts.filter((p) => p.generation === "982").length;
  const hash = db.getMeta("bundled_catalog_hash");

  assert(parts.length > 3000, `expected thousands of rows, got ${parts.length}`);
  assert(n981 > 1000, `981 count ${n981}`);
  assert(n982 > 1000, `982 count ${n982}`);
  assert(hash && hash.length === 64, "catalog hash missing");
  assert(oil?.name_zh?.includes("机油滤芯"), `oil zh=${oil?.name_zh}`);
  assert(/^Oil filter insert$/i.test(oil?.name_en || ""), `oil en=${oil?.name_en}`);
  assert(Math.abs((oil?.oem_price ?? NaN) - 28.61) < 0.005, `oil oem=${oil?.oem_price}`);
  assert(
    Math.abs((oil?.aftermarket_price ?? NaN) - 17.41) < 0.005,
    `oil am=${oil?.aftermarket_price}`,
  );
  assert(/petka_verified=0/.test(oil?.price_note || ""), "oil unverified");
  assert(!isPetkaPriceVerified(oil?.notes, oil?.price_note), "oil PETKA-verified");
  assert(oilBulk?.generation === "981", `oil bulk gen=${oilBulk?.generation}`);
  assert(oilBulk?.oem_price === 28.61, `oil bulk price=${oilBulk?.oem_price}`);
  assert(wheel?.name_zh?.includes("车轮支架"), `wheel zh=${wheel?.name_zh}`);
  assert(/^Wheel carrier/i.test(wheel?.name_en || ""), `wheel en=${wheel?.name_en}`);
  assert(
    Math.abs((wheel?.oem_price ?? NaN) - 784.02) < 0.005,
    `wheel oem=${wheel?.oem_price}`,
  );
  assert(/source=teile\.com/.test(wheel?.price_note || ""), "wheel source");

  const missing = uniqueLinkSkus(fixture).filter((sku) => !bySku.has(sku));
  if (missing.length) {
    throw new Error(
      `model-link SKUs missing (${missing.length}): ${missing.join(",")}`,
    );
  }
  db.close();
  return {
    total: parts.length,
    n981,
    n982,
    links: uniqueLinkSkus(fixture).length,
    km: null,
  };
}

async function main() {
  const fixture = materializeFixture();
  const freshDb = path.join(fixture, "fresh.db");
  const bootstrapDb = path.join(fixture, "bootstrap.db");
  const epcPath = path.join(
    fixture,
    "data/seed/petka/plaintext-archive/PETKA_Porsche_981_EPC.csv",
  );
  assertPetkaEpcFixtures(
    parsePetkaEpcCsv(fs.readFileSync(epcPath, "utf8")).byCompact,
  );

  try {
    const { child } = await startBridge(fixture, freshDb);
    let listed;
    try {
      listed = await rpcPartsList(child);
    } finally {
      await stopBridge(child);
    }
    assert(Array.isArray(listed), "parts:list not an array");
    const fresh = checkCatalogShape(freshDb, fixture);
    assert(listed.length === fresh.total, `list ${listed.length} vs db ${fresh.total}`);
    const listedWheel = listed.find((p) => p.sku === "981-99134115806");
    assert(
      Math.abs((listedWheel?.oem_price ?? NaN) - 784.02) < 0.005,
      `parts:list wheel ${listedWheel?.oem_price}`,
    );
    console.log(
      JSON.stringify(
        {
          label: "fresh",
          total: fresh.total,
          gen981: fresh.n981,
          gen982: fresh.n982,
          modelLinks: fresh.links,
          partsList: listed.length,
        },
        null,
        2,
      ),
    );

    const { child: child2 } = await startBridge(fixture, freshDb);
    await stopBridge(child2);
    const again = checkCatalogShape(freshDb, fixture);
    assert(again.total === fresh.total, `repeat ${again.total} vs ${fresh.total}`);

    const boot = new GarageDb(bootstrapDb);
    const partsSeed = JSON.parse(
      fs.readFileSync(path.join(fixture, "data/seed/parts/bootstrap.json"), "utf8"),
    );
    const faultsSeed = JSON.parse(
      fs.readFileSync(path.join(fixture, "data/seed/faults/bootstrap.json"), "utf8"),
    );
    boot.seedIfEmpty({ parts: partsSeed.parts, faults: faultsSeed.faults });
    boot.setMileage(42_000);
    boot.addServiceRecord({
      title: "用户记录",
      replaced_at: "2026-02-02",
      odometer_km: 40_000,
    });
    assert(boot.listParts().length < 50, "bootstrap-only should be small");
    boot.close();

    const { child: child3 } = await startBridge(fixture, bootstrapDb);
    await stopBridge(child3);
    const upgraded = checkCatalogShape(bootstrapDb, fixture);
    const up = new GarageDb(bootstrapDb);
    assert(up.getVehicle().current_km === 42_000, "vehicle km overwritten");
    assert(
      up.listServiceRecords().some((r) => r.title === "用户记录"),
      "service record lost",
    );
    up.close();

    console.log(
      JSON.stringify(
        {
          ok: true,
          fixture,
          fresh: fresh.total,
          repeat: again.total,
          upgraded: upgraded.total,
          gen981: fresh.n981,
          gen982: fresh.n982,
          modelLinks: fresh.links,
          partsList: listed.length,
          wheel: 784.02,
        },
        null,
        2,
      ),
    );
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
