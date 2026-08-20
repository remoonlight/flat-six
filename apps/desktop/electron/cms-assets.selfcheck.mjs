/**
 * Self-check: cms-assets mesh-map upsert/remove roundtrip.
 * Run: node apps/desktop/electron/cms-assets.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  cmsHintForZone,
  listCmsAssets,
  loadMeshMap,
  removeMeshLink,
  upsertMeshLink,
  ZONE_DEFAULT_ASSET,
} from "./cms-assets.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../../..");
const mapPath = path.join(root, ".local", "cms-mesh-map.json");
const backup = mapPath + ".selfcheck.bak";

if (fs.existsSync(mapPath)) {
  fs.copyFileSync(mapPath, backup);
}

try {
  const assets = listCmsAssets();
  if (!assets.length) throw new Error("no cms assets catalog");
  // 产品幽灵壳不用 CMS 991 车身；允许 browseOnly 对照浏览
  const bodyRows = assets.filter((a) =>
    String(a.rel).includes("porsche991_body"),
  );
  for (const a of bodyRows) {
    if (!a.browseOnly) {
      throw new Error("cms_991_body_must_be_browse_only");
    }
  }
  if (Object.values(ZONE_DEFAULT_ASSET).includes("cms991body")) {
    throw new Error("cms_991_body_must_not_be_zone_default");
  }
  // 四座舱 id（seed）须仍能在 catalog 按 rel 对上；flat-six 路径不得挂 991 body
  const seedPath = path.join(root, "data", "seed", "flat-six", "cabins.json");
  const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
  const expectedIds = [
    "cabin-981-boxster",
    "cabin-981-cayman",
    "cabin-982-boxster",
    "cabin-982-cayman",
  ];
  const seedCabins = Array.isArray(seed.cabins) ? seed.cabins : [];
  for (const id of expectedIds) {
    const c = seedCabins.find((x) => x.id === id);
    if (!c) throw new Error(`cabin_seed_missing:${id}`);
    if (String(c.rel || "").includes("porsche991_body")) {
      throw new Error(`flat_six_must_not_use_991_body:${id}`);
    }
    if (!assets.some((a) => a.rel === c.rel)) {
      throw new Error(`cabin_not_in_catalog:${id}:${c.rel}`);
    }
  }
  if (!assets.some((a) => a.id === "cabin981") || !assets.some((a) => a.id === "cabin982")) {
    throw new Error("flat_six_cabins_required_for_interior_screening");
  }
  // flat-six.org/garage 清单：browseOnly 条目不得进 zone 默认
  const garagePath = path.join(root, "data", "seed", "flat-six", "garage-models.json");
  const garage = JSON.parse(fs.readFileSync(garagePath, "utf8"));
  const garageModels = Array.isArray(garage.models) ? garage.models : [];
  if (garageModels.length < 20) throw new Error("garage_models_incomplete");
  const zoneDefaults = new Set(Object.values(ZONE_DEFAULT_ASSET));
  for (const m of garageModels) {
    if (!m?.browseOnly) continue;
    if (!assets.some((a) => a.id === m.id && a.rel === m.rel && a.browseOnly)) {
      throw new Error(`garage_browse_missing:${m.id}`);
    }
    if (zoneDefaults.has(m.id)) {
      throw new Error(`garage_browse_must_not_be_zone_default:${m.id}`);
    }
  }
  // 981 底盘/排气 PETKA 图号分件：browseOnly，不得进 zone 默认
  const petkaPath = path.join(
    root,
    "data",
    "seed",
    "petka-models",
    "chassis-exhaust.json",
  );
  const petka = JSON.parse(fs.readFileSync(petkaPath, "utf8"));
  const petkaModels = Array.isArray(petka.models) ? petka.models : [];
  if (petkaModels.length !== 19) throw new Error("petka_chassis_exhaust_incomplete");
  for (const m of petkaModels) {
    if (!m?.browseOnly) throw new Error(`petka_must_browse_only:${m?.id}`);
    if (!assets.some((a) => a.id === m.id && a.rel === m.rel && a.browseOnly)) {
      throw new Error(`petka_browse_missing:${m.id}`);
    }
    if (zoneDefaults.has(m.id)) {
      throw new Error(`petka_browse_must_not_be_zone_default:${m.id}`);
    }
  }
  const hint = cmsHintForZone("engine-bay");
  if (hint.assetId !== "engine") throw new Error("engine-bay default asset");
  const interiorHint = cmsHintForZone("interior");
  if (interiorHint.assetId !== "cabin981") {
    throw new Error("interior default asset must be cabin981");
  }
  const frontTrunkHint = cmsHintForZone("front-trunk");
  if (frontTrunkHint.assetId !== "body") {
    throw new Error("front-trunk default asset must be body");
  }
  const electronicsHint = cmsHintForZone("electronics");
  if (electronicsHint.assetId !== "body") {
    throw new Error("electronics default asset must be body");
  }
  const fluidsHint = cmsHintForZone("fluids");
  if (fluidsHint.assetId !== "engine") {
    throw new Error("fluids default asset must be engine");
  }

  const marker = `selfcheck-${Date.now()}`;
  const after = upsertMeshLink({
    zoneId: "engine-bay",
    meshName: marker,
    hotspotId: "engine-bay",
    assetId: "engine",
    sku: "oil-filter",
    note: "selfcheck",
  });
  if (!after.links.some((l) => l.meshName === marker && l.sku === "oil-filter")) {
    throw new Error("upsert missing");
  }

  const cleared = removeMeshLink("engine-bay", marker);
  if (cleared.links.some((l) => l.meshName === marker)) {
    throw new Error("remove failed");
  }

  // restore prior map if any; else delete selfcheck residue
  if (fs.existsSync(backup)) {
    fs.copyFileSync(backup, mapPath);
    fs.unlinkSync(backup);
  } else if (fs.existsSync(mapPath)) {
    const cur = loadMeshMap();
    if (cur.links.length === 0 && !cur.updated_at) {
      /* leave empty */
    }
  }

  console.log(
    `CMS ASSETS SELFCHECK PASS; present=${assets.filter((a) => a.present).map((a) => a.id).join(",") || "none"}`,
  );
} catch (e) {
  if (fs.existsSync(backup)) {
    fs.copyFileSync(backup, mapPath);
    fs.unlinkSync(backup);
  }
  console.error(e);
  process.exit(1);
}
