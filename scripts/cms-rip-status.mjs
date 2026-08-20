/**
 * Detect CMS2021 + Porsche DLC install and local rip progress.
 *
 * Usage: npm run status:cms-rip
 *        node scripts/cms-rip-status.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const manifestPath = path.join(root, "data", "seed", "cms-rip", "manifest.json");
const LOCAL_RIP = path.join(root, ".local", "cms-rip");

const STEAM_VDFS = [
  path.join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Steam", "steamapps", "libraryfolders.vdf"),
  path.join(process.env.ProgramFiles || "C:\\Program Files", "Steam", "steamapps", "libraryfolders.vdf"),
];

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function parseLibraryFolders(vdfText) {
  const libs = [];
  const re = /"path"\s+"([^"]+)"/g;
  let m;
  while ((m = re.exec(vdfText))) {
    libs.push(m[1].replace(/\\\\/g, "\\"));
  }
  return libs;
}

function findCmsInstall(installDirName) {
  const tried = [];
  const candidates = [];

  for (const vdf of STEAM_VDFS) {
    if (!fs.existsSync(vdf)) continue;
    try {
      for (const lib of parseLibraryFolders(fs.readFileSync(vdf, "utf8"))) {
        candidates.push(path.join(lib, "steamapps", "common", installDirName));
      }
    } catch {
      /* ignore bad vdf */
    }
  }

  for (const drive of ["C", "D", "E", "F"]) {
    candidates.push(
      path.join(`${drive}:\\SteamLibrary`, "steamapps", "common", installDirName),
      path.join(`${drive}:\\Steam`, "steamapps", "common", installDirName),
      path.join(`${drive}:\\Program Files (x86)\\Steam`, "steamapps", "common", installDirName),
    );
  }

  const seen = new Set();
  for (const c of candidates) {
    const norm = path.normalize(c);
    if (seen.has(norm)) continue;
    seen.add(norm);
    tried.push(norm);
    if (fs.existsSync(norm)) {
      return { found: true, installRoot: norm, tried };
    }
  }
  return { found: false, installRoot: null, tried };
}

function dirHasNameMatch(dir, needles) {
  if (!dir || !fs.existsSync(dir)) return [];
  const hits = [];
  const walk = (d, depth) => {
    if (depth > 3) return;
    let entries;
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      const full = path.join(d, ent.name);
      const lower = ent.name.toLowerCase();
      if (needles.some((n) => lower.includes(n))) hits.push(full);
      if (ent.isDirectory() && !ent.name.startsWith(".")) walk(full, depth + 1);
    }
  };
  walk(dir, 0);
  return hits.slice(0, 40);
}

function main() {
  const manifest = readJson(manifestPath);
  const { installDirName } = manifest.steam;
  const install = findCmsInstall(installDirName);

  const dataDir = install.found
    ? path.join(install.installRoot, "Car Mechanic Simulator 2021_Data")
    : null;
  const streaming = dataDir ? path.join(dataDir, "StreamingAssets") : null;

  const porscheNeedles = ["porsche", "b61", "ma1", "991", "carrera"];
  const streamingHits = streaming ? dirHasNameMatch(streaming, porscheNeedles) : [];

  const engineGlb = path.join(root, manifest.paths.engineGlbRel);
  const chassisGlb = path.join(root, ".local", "cms-rip", "porsche991_chassis", "chassis.glb");
  const bodyGlb = path.join(root, ".local", "cms-rip", "porsche991_body", "body.glb");
  const rawDir = path.join(root, manifest.paths.rawExportRel);
  const partsDir = path.join(root, manifest.paths.partsDirRel);

  const report = {
    ok: true,
    target: manifest.target,
    filters: manifest.assetStudioFilters,
    caveatZh: manifest.caveatZh,
    install: {
      found: install.found,
      root: install.installRoot,
      dataDir: dataDir && fs.existsSync(dataDir) ? dataDir : null,
      streamingAssets: streaming && fs.existsSync(streaming) ? streaming : null,
      porscheNameHitsSample: streamingHits.map((p) =>
        path.relative(install.installRoot || "", p).replace(/\\/g, "/"),
      ),
    },
    rip: {
      localRipDir: path.relative(root, LOCAL_RIP).replace(/\\/g, "/"),
      rawExportExists: fs.existsSync(rawDir),
      engineGlb: fs.existsSync(engineGlb),
      engineGlbRel: manifest.paths.engineGlbRel,
      chassisGlb: fs.existsSync(chassisGlb),
      chassisGlbRel: ".local/cms-rip/porsche991_chassis/chassis.glb",
      bodyGlb: fs.existsSync(bodyGlb),
      bodyGlbRel: ".local/cms-rip/porsche991_body/body.glb",
      bodyForApp: false,
      partsDirExists: fs.existsSync(partsDir),
      partFileCount: fs.existsSync(partsDir)
        ? fs.readdirSync(partsDir).filter((f) => /\.(glb|obj|fbx)$/i.test(f)).length
        : 0,
    },
    next: !install.found
      ? "Install CMS2021 + Porsche Remastered DLC on Steam, then re-run status:cms-rip"
      : fs.existsSync(engineGlb) && fs.existsSync(chassisGlb)
        ? "App-ready: engine+chassis mounted; body=flat-six (CMS 991 body not for App)"
        : "npm run export:cms-rip  (UnityPy → engine + chassis; body optional/unused by App)",
  };

  if (!fs.existsSync(path.dirname(LOCAL_RIP))) {
    /* do not create .local here unless writing; status is read-only-ish */
  }

  console.log(JSON.stringify(report, null, 2));
  console.log("");
  console.log(
    install.found
      ? `CMS: ${install.installRoot}`
      : "CMS: not found (checked Steam libraries + common drives)",
  );
  console.log(
    `engine.glb: ${report.rip.engineGlb ? "YES" : "NO"} | chassis.glb: ${report.rip.chassisGlb ? "YES" : "NO"} | body.glb: ${report.rip.bodyGlb ? "YES (unused by App)" : "NO (ok)"}`,
  );
  console.log(`next: ${report.next}`);
}

main();
