/**
 * 3D posture-A / hotspot-enough status (not accept).
 * Prints JSON + one-line summary; writes .local/mesh-map-status.json
 *
 * Usage: npm run status:mesh-map
 *        node scripts/status-mesh-map.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL = path.join(root, ".local");
const OUT = path.join(LOCAL, "mesh-map-status.json");
const REPORT_REL = ".local/mesh-map-status.json";

const UNMAPPED_CAP = 40;
const ENG_HOTSPOTS = ["eng-intake", "eng-block", "eng-exhaust"];
const ENGINE_BAY_REQUIRED = ["engine-bay", ...ENG_HOTSPOTS];

const ENGINE_GLB = path.join(LOCAL, "cms-rip", "engine_b61_porsche", "engine.glb");
const CHASSIS_GLB = path.join(LOCAL, "cms-rip", "porsche991_chassis", "chassis.glb");
const CABIN_GLB = path.join(LOCAL, "flat-six", "boxster-real.glb");
const TRANSFORMS = path.join(LOCAL, "xray-transforms.json");
const MESH_MAP = path.join(LOCAL, "cms-mesh-map.json");
const ASSEMBLIES = path.join(root, "data", "seed", "xray", "assemblies.json");
const ZONES = path.join(root, "data", "seed", "locator", "zones.json");
const BOOTSTRAP = path.join(root, "data", "seed", "parts", "bootstrap.json");

function rel(p) {
  return path.relative(root, p).replace(/\\/g, "/");
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

/** Minimal GLB → glTF JSON (12-byte header + JSON chunk). No deps. */
function parseGlbJson(absPath) {
  const buf = fs.readFileSync(absPath);
  if (buf.length < 12) throw new Error("glb_too_short");
  const magic = buf.toString("ascii", 0, 4);
  if (magic !== "glTF") throw new Error("glb_bad_magic");
  const version = buf.readUInt32LE(4);
  if (version !== 2) throw new Error(`glb_version_${version}`);
  let offset = 12;
  while (offset + 8 <= buf.length) {
    const chunkLen = buf.readUInt32LE(offset);
    const chunkType = buf.toString("ascii", offset + 4, offset + 8);
    const dataStart = offset + 8;
    const dataEnd = dataStart + chunkLen;
    if (dataEnd > buf.length) throw new Error("glb_chunk_truncated");
    if (chunkType === "JSON") {
      const text = buf.toString("utf8", dataStart, dataEnd).replace(/\0+$/, "");
      return JSON.parse(text);
    }
    offset = dataEnd;
  }
  throw new Error("glb_no_json_chunk");
}

/** Collect node + mesh names from a glTF JSON. */
function collectMeshNames(gltf) {
  const names = new Set();
  for (const n of gltf.nodes || []) {
    if (n?.name) names.add(String(n.name));
  }
  for (const m of gltf.meshes || []) {
    if (m?.name) names.add(String(m.name));
  }
  return [...names].sort();
}

function buildReport() {
  const zonesDoc = readJson(ZONES);
  const bootstrap = readJson(BOOTSTRAP);
  const zones = zonesDoc.zones || [];

  const hotspotByZone = new Map();
  const allHotspotIds = new Set();
  for (const z of zones) {
    const ids = (z.hotspots || []).map((h) => h.id);
    hotspotByZone.set(z.id, new Set(ids));
    for (const id of ids) allHotspotIds.add(id);
  }

  const engineBayHs = hotspotByZone.get("engine-bay") || new Set();
  const engineBayRequired = {};
  for (const id of ENGINE_BAY_REQUIRED) {
    engineBayRequired[id] = engineBayHs.has(id);
  }

  const bootstrapHotspots = [
    ...new Set(
      (bootstrap.parts || [])
        .map((p) => p.locator_hotspot)
        .filter((h) => typeof h === "string" && h),
    ),
  ].sort();
  const onMap = [];
  const offMap = [];
  for (const h of bootstrapHotspots) {
    if (allHotspotIds.has(h)) onMap.push(h);
    else offMap.push(h);
  }

  let assembliesLoadable = false;
  let assembliesError = null;
  let assemblyCount = 0;
  try {
    const ass = readJson(ASSEMBLIES);
    assemblyCount = Array.isArray(ass.assemblies) ? ass.assemblies.length : 0;
    assembliesLoadable = assemblyCount > 0;
  } catch (e) {
    assembliesError = String(e?.message || e);
  }

  const posture = {
    engineGlb: fs.existsSync(ENGINE_GLB),
    chassisGlb: fs.existsSync(CHASSIS_GLB),
    cabin981Glb: fs.existsSync(CABIN_GLB),
    xrayTransforms: fs.existsSync(TRANSFORMS) ? "present" : "missing",
    xrayAssemblies: {
      loadable: assembliesLoadable,
      path: rel(ASSEMBLIES),
      assemblyCount,
      error: assembliesError,
    },
  };

  let meshMapPresent = fs.existsSync(MESH_MAP);
  let meshMap = { version: 1, links: [] };
  let meshMapError = null;
  if (meshMapPresent) {
    try {
      meshMap = readJson(MESH_MAP);
      if (!Array.isArray(meshMap.links)) meshMap.links = [];
    } catch (e) {
      meshMapError = String(e?.message || e);
      meshMapPresent = false;
      meshMap = { version: 1, links: [] };
    }
  }

  const linksByZone = {};
  const engHotspotMeshLinks = Object.fromEntries(ENG_HOTSPOTS.map((id) => [id, 0]));
  const mappedFromLinks = new Set();
  for (const link of meshMap.links || []) {
    const z = link.zoneId || "?";
    linksByZone[z] = (linksByZone[z] || 0) + 1;
    if (link.meshName) mappedFromLinks.add(String(link.meshName));
    if (ENG_HOTSPOTS.includes(link.hotspotId)) {
      engHotspotMeshLinks[link.hotspotId] += 1;
    }
  }

  const meshes = { parsed: false, byAsset: {}, meshCount: 0, mappedMeshNames: [], unmappedMeshNames: [], unmappedTruncated: false, unmappedTotal: 0, error: null };
  const glbTargets = [
    { id: "engine", abs: ENGINE_GLB },
    { id: "chassis", abs: CHASSIS_GLB },
  ];
  const presentGlbs = glbTargets.filter((t) => fs.existsSync(t.abs));
  if (presentGlbs.length > 0) {
    try {
      const allNames = new Set();
      for (const t of presentGlbs) {
        const gltf = parseGlbJson(t.abs);
        const names = collectMeshNames(gltf);
        meshes.byAsset[t.id] = { path: rel(t.abs), nameCount: names.length, namesSample: names.slice(0, 5) };
        for (const n of names) allNames.add(n);
      }
      const sorted = [...allNames].sort();
      meshes.parsed = true;
      meshes.meshCount = sorted.length;
      const mapped = [];
      const unmapped = [];
      for (const n of sorted) {
        if (mappedFromLinks.has(n)) mapped.push(n);
        else unmapped.push(n);
      }
      meshes.mappedMeshNames = mapped;
      meshes.unmappedTotal = unmapped.length;
      meshes.unmappedTruncated = unmapped.length > UNMAPPED_CAP;
      meshes.unmappedMeshNames = unmapped.slice(0, UNMAPPED_CAP);
    } catch (e) {
      meshes.error = String(e?.message || e);
    }
  }

  const engNav = {
    hotspotsPresent: Object.fromEntries(
      ENG_HOTSPOTS.map((id) => [id, engineBayHs.has(id)]),
    ),
    allPresent: ENG_HOTSPOTS.every((id) => engineBayHs.has(id)),
    meshLinks: engHotspotMeshLinks,
    suggestedNext:
      "Locator 单模型 engine 点选 mesh 写入对应到 eng-intake / eng-block / eng-exhaust",
  };

  const nextActions = [];
  if (!posture.engineGlb) nextActions.push("缺少 engine.glb（.local/cms-rip/engine_b61_porsche/）");
  if (!posture.chassisGlb) nextActions.push("缺少 chassis.glb（.local/cms-rip/porsche991_chassis/）");
  if (!posture.cabin981Glb) nextActions.push("缺少 cabin981 GLB（.local/flat-six/boxster-real.glb）");
  if (posture.xrayTransforms === "missing") {
    nextActions.push(
      "复制 data/seed/xray/transforms.template.json → .local/xray-transforms.json 后手调姿态",
    );
  }
  if (!assembliesLoadable) nextActions.push("xray assemblies.json 无法加载");
  for (const id of ENGINE_BAY_REQUIRED) {
    if (!engineBayRequired[id]) nextActions.push(`zones.json engine-bay 缺热点 ${id}`);
  }
  for (const id of ENG_HOTSPOTS) {
    if (engHotspotMeshLinks[id] === 0) {
      nextActions.push(`eng-* 无 mesh link：${id}（Locator 单模型 engine 手对）`);
    }
  }
  if (offMap.length) {
    nextActions.push(`bootstrap locator_hotspot offMap: ${offMap.join(", ")}`);
  }
  if (!meshMapPresent) nextActions.push("尚无 .local/cms-mesh-map.json（可手对后自动创建）");
  if (meshMapError) nextActions.push(`cms-mesh-map.json 损坏: ${meshMapError}`);
  if (meshes.error) nextActions.push(`GLB 解析失败: ${meshes.error}`);

  return {
    ok: true,
    generatedAt: new Date().toISOString(),
    policy: {
      mode: "posture-A",
      mapping: "hotspot-enough",
      reportPath: REPORT_REL,
    },
    posture,
    hotspots: {
      engineBayRequired,
      bootstrapHotspots,
      onMap,
      offMap,
      offMapCount: offMap.length,
    },
    meshMap: {
      present: meshMapPresent,
      path: REPORT_REL.replace("mesh-map-status.json", "cms-mesh-map.json"),
      linkCount: (meshMap.links || []).length,
      linksByZone,
      engHotspotMeshLinks,
      error: meshMapError,
    },
    meshes,
    engNav,
    nextActions,
  };
}

function printSummary(report) {
  const p = report.posture;
  const glb =
    `engine=${p.engineGlb ? "y" : "n"} chassis=${p.chassisGlb ? "y" : "n"} cabin=${p.cabin981Glb ? "y" : "n"}`;
  const engLinks = ENG_HOTSPOTS.map((id) => `${id}=${report.meshMap.engHotspotMeshLinks[id]}`).join(" ");
  const unmapped =
    report.meshes.parsed
      ? `unmapped=${report.meshes.unmappedTotal}${report.meshes.unmappedTruncated ? `(cap${UNMAPPED_CAP})` : ""}`
      : "unmapped=n/a";
  console.log(
    `mesh-map status: posture-A | glb ${glb} | transforms=${p.xrayTransforms} | offMap=${report.hotspots.offMapCount} | ${engLinks} | meshCount=${report.meshes.meshCount} ${unmapped} | next=${report.nextActions.length} | wrote ${REPORT_REL}`,
  );
}

function main() {
  fs.mkdirSync(LOCAL, { recursive: true });
  const report = buildReport();
  fs.writeFileSync(OUT, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(report, null, 2));
  console.log("");
  printSummary(report);
  process.exit(0);
}

try {
  main();
} catch (err) {
  console.error(err);
  process.exit(1);
}
