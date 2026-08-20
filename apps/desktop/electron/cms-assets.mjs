/**
 * Local GLB catalog (CMS rip + flat-six) + mesh↔hotspot map under .local/.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const LOCAL_ROOT = path.join(repoRoot, ".local");
const MESH_MAP_PATH = path.join(LOCAL_ROOT, "cms-mesh-map.json");
const GARAGE_MODELS_PATH = path.join(
  repoRoot,
  "data",
  "seed",
  "flat-six",
  "garage-models.json",
);
const PETKA_MODELS_PATH = path.join(
  repoRoot,
  "data",
  "seed",
  "petka-models",
  "chassis-exhaust.json",
);

/**
 * flat-six.org/garage 外观 GLB（browseOnly）；产品 zone 默认仍用下方硬编码条目。
 * @returns {ReadonlyArray<{ id: string; label_zh: string; rel: string; attribution?: string; browseOnly?: boolean }>}
 */
function loadGarageBrowseAssets() {
  try {
    const seed = JSON.parse(fs.readFileSync(GARAGE_MODELS_PATH, "utf8"));
    const models = Array.isArray(seed.models) ? seed.models : [];
    return models
      .filter((m) => m && m.browseOnly !== false && m.rel && m.id)
      .map((m) => ({
        id: String(m.id),
        label_zh: `${m.id} · ${m.label_zh || m.id}${m.license ? ` (${m.license})` : ""}`,
        rel: String(m.rel),
        attribution: m.attribution
          ? `${m.attribution}${m.license ? ` (${m.license})` : ""} via flat-six.org`
          : undefined,
        browseOnly: true,
      }));
  } catch {
    return [];
  }
}

/**
 * 981 底盘/排气 PETKA 图号分件（browseOnly）。
 * @returns {ReadonlyArray<{ id: string; label_zh: string; rel: string; browseOnly?: boolean }>}
 */
function loadPetkaChassisExhaustAssets() {
  try {
    const seed = JSON.parse(fs.readFileSync(PETKA_MODELS_PATH, "utf8"));
    const models = Array.isArray(seed.models) ? seed.models : [];
    return models
      .filter((m) => m && m.browseOnly === true && m.rel && m.id)
      .map((m) => ({
        id: String(m.id),
        label_zh: String(m.label_zh || m.id),
        rel: String(m.rel),
        browseOnly: true,
      }));
  } catch {
    return [];
  }
}

/**
 * rel is under `.local/` and must start with cms-rip/ / flat-six/ / petka-models/.
 * browseOnly: Locator 单模型可看可点；不得作 zone 默认 / 产品幽灵壳。
 * @type {ReadonlyArray<{ id: string; label_zh: string; rel: string; attribution?: string; browseOnly?: boolean }>}
 */
export const CMS_ASSETS = [
  {
    id: "engine",
    label_zh: "CMS 991.2 引擎 MA1.03",
    rel: "cms-rip/engine_b61_porsche/engine.glb",
  },
  {
    id: "chassis",
    label_zh: "CMS 991.2 底盘/制动",
    rel: "cms-rip/porsche991_chassis/chassis.glb",
  },
  {
    // 对照浏览：非产品幽灵壳（产品车身仍用 flat-six 981）
    id: "cms991body",
    label_zh: "CMS 991.2 车身(对照·非产品)",
    rel: "cms-rip/porsche991_body/body.glb",
    browseOnly: true,
  },
  {
    // 981 主座舱（免费；SM_Interior_* 材质子块，手拼主用）
    id: "cabin981",
    label_zh: "cabin-981-boxster · 981 座舱主用",
    rel: "flat-six/boxster-real.glb",
    attribution:
      "「2015 Porsche Boxster GTS」by Ddiaz Design (CC BY 4.0) via flat-six.org / Sketchfab",
  },
  {
    // 兼容旧 id「body」→ 同文件
    id: "body",
    label_zh: "cabin-981-boxster · 981 车身",
    rel: "flat-six/boxster-real.glb",
    attribution:
      "「2015 Porsche Boxster GTS」by Ddiaz Design (CC BY 4.0) via flat-six.org / Sketchfab",
  },
  {
    id: "cayman981",
    label_zh: "cabin-981-cayman · 981 Cayman 外板",
    rel: "flat-six/cayman-981.glb",
    attribution:
      "「2014 Porsche Cayman S (981)」by Ddiaz Design (CC BY 4.0) via flat-six.org / Sketchfab",
  },
  {
    // 982/718 对照保留，默认不绑 zone；内饰手拼主用 cabin981
    id: "cabin982",
    label_zh: "cabin-982-boxster · 718 座舱对照",
    rel: "flat-six/boxster-s-982.glb",
    attribution:
      "「2017 Porsche 718 Boxster S」by Ddiaz Design (CC BY 4.0) via flat-six.org / Sketchfab",
  },
  {
    id: "cayman982",
    label_zh: "cabin-982-cayman · 718 Cayman 对照",
    rel: "flat-six/cayman-982.glb",
    attribution:
      "「2017 Porsche 718 Cayman (982)」by Ddiaz Design (CC BY 4.0) via flat-six.org / Sketchfab",
  },
  // flat-six.org/garage 其余独立外观 GLB（含 NC-SA：本机浏览 only）
  ...loadGarageBrowseAssets(),
  // 981 底盘/排气 PETKA 图号分件（Tripo；本机浏览 only）
  ...loadPetkaChassisExhaustAssets(),
];

/** Default asset id per locator zone. */
export const ZONE_DEFAULT_ASSET = {
  "engine-bay": "engine",
  brakes: "chassis",
  chassis: "chassis",
  interior: "cabin981",
  // 外板示意；与 cabin981 同 flat-six 文件，无独立前备箱 GLB
  "front-trunk": "body",
  // P-Loc-2 草稿：无独立电子/循环 GLB，用现有示意
  electronics: "body",
  fluids: "engine",
};

function absForRel(rel) {
  const normalized = String(rel || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (
    !normalized ||
    normalized.includes("..") ||
    path.isAbsolute(normalized)
  ) {
    throw new Error("cms_rel_invalid");
  }
  if (
    !normalized.startsWith("cms-rip/") &&
    !normalized.startsWith("flat-six/") &&
    !normalized.startsWith("petka-models/")
  ) {
    throw new Error("cms_rel_root");
  }
  const abs = path.normalize(path.join(LOCAL_ROOT, normalized));
  if (!abs.startsWith(LOCAL_ROOT)) throw new Error("cms_rel_escape");
  return { normalized, abs };
}

export function listCmsAssets() {
  return CMS_ASSETS.map((a) => {
    const { abs } = absForRel(a.rel);
    return {
      ...a,
      present: fs.existsSync(abs),
      bytes: fs.existsSync(abs) ? fs.statSync(abs).size : 0,
    };
  });
}

export function resolveCmsRel(rel) {
  const { normalized, abs } = absForRel(rel);
  if (!fs.existsSync(abs)) throw new Error(`cms_glb_missing:${normalized}`);
  return abs;
}

export function readCmsGlb(rel) {
  return fs.readFileSync(resolveCmsRel(rel));
}

function emptyMeshMap() {
  return {
    version: 1,
    note: "mesh name → locator hotspot（本机手对；勿提交）",
    caveatZh:
      "CMS 引擎/底盘为 991 示意；991 车身仅 Locator 对照浏览（browseOnly）。产品车身/座舱为 flat-six（CC BY；982 供内饰筛选）",
    updated_at: null,
    links: [],
  };
}

export function loadMeshMap() {
  if (!fs.existsSync(MESH_MAP_PATH)) return emptyMeshMap();
  try {
    const raw = JSON.parse(fs.readFileSync(MESH_MAP_PATH, "utf8"));
    return {
      ...emptyMeshMap(),
      ...raw,
      links: Array.isArray(raw.links) ? raw.links : [],
    };
  } catch {
    return emptyMeshMap();
  }
}

export function saveMeshMap(map) {
  const next = {
    version: 1,
    note: map?.note || emptyMeshMap().note,
    caveatZh: map?.caveatZh || emptyMeshMap().caveatZh,
    updated_at: new Date().toISOString(),
    links: Array.isArray(map?.links) ? map.links : [],
  };
  fs.mkdirSync(path.dirname(MESH_MAP_PATH), { recursive: true });
  fs.writeFileSync(MESH_MAP_PATH, JSON.stringify(next, null, 2) + "\n", "utf8");
  return next;
}

/**
 * @param {{ zoneId: string; meshName: string; hotspotId: string; assetId?: string | null; sku?: string | null; note?: string | null }} link
 */
export function upsertMeshLink(link) {
  const zoneId = String(link.zoneId || "");
  const meshName = String(link.meshName || "");
  const hotspotId = String(link.hotspotId || "");
  if (!zoneId || !meshName || !hotspotId) {
    throw new Error("cms_link_incomplete");
  }
  const map = loadMeshMap();
  const row = {
    zoneId,
    meshName,
    hotspotId,
    assetId: link.assetId ? String(link.assetId) : null,
    sku: link.sku ? String(link.sku) : null,
    note: link.note ? String(link.note) : null,
  };
  const i = map.links.findIndex(
    (l) => l.zoneId === zoneId && l.meshName === meshName,
  );
  if (i >= 0) map.links[i] = { ...map.links[i], ...row };
  else map.links.push(row);
  return saveMeshMap(map);
}

export function removeMeshLink(zoneId, meshName) {
  const map = loadMeshMap();
  map.links = map.links.filter(
    (l) => !(l.zoneId === zoneId && l.meshName === meshName),
  );
  return saveMeshMap(map);
}

export function cmsHintForZone(zoneId) {
  const assetId = ZONE_DEFAULT_ASSET[zoneId] || null;
  const asset = CMS_ASSETS.find((a) => a.id === assetId) || null;
  if (!asset) {
    return {
      present: false,
      assetId: null,
      rel: null,
      caveatZh: emptyMeshMap().caveatZh,
    };
  }
  const { abs } = absForRel(asset.rel);
  return {
    present: fs.existsSync(abs),
    assetId: asset.id,
    rel: asset.rel,
    caveatZh: emptyMeshMap().caveatZh,
  };
}
