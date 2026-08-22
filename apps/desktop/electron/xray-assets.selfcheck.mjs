/**
 * Self-check: xray seed + path escape + transform/mesh-state roundtrip.
 * Run: node apps/desktop/electron/xray-assets.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  _test,
  bridgeForAssembly,
  listGarageXrayLayers,
  listXrayAssemblies,
  listXrayLayers,
  loadXrayMeshState,
  loadXrayTransforms,
  layerTransformAssemblyId,
  meshStateAssemblyId,
  setXrayLayerVisible,
  setXrayMeshEntry,
  setXrayTransform,
} from "./xray-assets.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../../..");
const tfPath = path.join(root, ".local", "xray-transforms.json");

/** GLB JSON chunk contains mesh node name (no Three loader). */
function glbJsonHas(abs, needle) {
  const buf = fs.readFileSync(abs);
  if (buf.length < 20 || buf.toString("ascii", 0, 4) !== "glTF") return false;
  const jsonLen = buf.readUInt32LE(12);
  return buf.subarray(20, 20 + jsonLen).toString("utf8").includes(needle);
}
const meshPath = path.join(root, ".local", "xray-mesh-state.json");
const backup = tfPath + ".selfcheck.bak";
const meshBackup = meshPath + ".selfcheck.bak";

if (fs.existsSync(tfPath)) {
  fs.copyFileSync(tfPath, backup);
}
if (fs.existsSync(meshPath)) {
  fs.copyFileSync(meshPath, meshBackup);
}

try {
  const seed = _test.loadSeed();
  if (!seed.axle?.frontZ) throw new Error("axle missing");
  const petkaAsm = seed.assemblies.filter((a) =>
    String(a.glbRel || "").startsWith("petka-models/"),
  );
  // 水冷+燃油+进气+引擎+悬架(含801)+传动+机油冷却 + 内饰807 + 碳罐 + 604（CMS 另计）
  if (petkaAsm.length !== 11) {
    throw new Error(`locator assemblies expect 11 petka, got ${petkaAsm.length}`);
  }
  const cooling = petkaAsm.find((a) => a.id === "pm-cooling");
  if (!cooling || !String(cooling.glbRel || "").includes("merged/cooling.glb")) {
    throw new Error("pm-cooling missing");
  }
  const fuel = petkaAsm.find((a) => a.id === "pm-fuel");
  if (!fuel || !String(fuel.glbRel || "").includes("merged/fuel.glb")) {
    throw new Error("pm-fuel missing");
  }
  const eng = petkaAsm.find((a) => a.id === "pm-engine");
  if (!eng || !String(eng.glbRel || "").includes("merged/engine.glb")) {
    throw new Error("pm-engine missing");
  }
  const intake = petkaAsm.find((a) => a.id === "pm-intake");
  if (!intake || !String(intake.glbRel || "").includes("merged/intake.glb")) {
    throw new Error("pm-intake missing merged/intake.glb");
  }
  if (petkaAsm.some((a) => a.id === "pm-105-020")) {
    throw new Error("pm-105-020 should be baked into pm-intake");
  }
  const susp = petkaAsm.find((a) => a.id === "pm-suspension");
  if (!susp || !String(susp.glbRel || "").includes("merged/suspension.glb")) {
    throw new Error("pm-suspension missing");
  }
  const driveline = petkaAsm.find((a) => a.id === "pm-302-000");
  if (
    !driveline ||
    !String(driveline.glbRel || "").includes("merged/driveline.glb")
  ) {
    throw new Error("pm-302-000 missing merged/driveline.glb");
  }
  for (const id of [
    "pm-302-000",
    "pm-360-000",
    "pm-807-000",
    "pm-807-005",
    "pm-201-020",
    "pm-604-000",
  ]) {
    if (!petkaAsm.some((a) => a.id === id)) throw new Error(`missing ${id}`);
  }
  if (petkaAsm.some((a) => a.id === "pm-801-020")) {
    throw new Error("pm-801-020 should be baked into pm-suspension");
  }
  if (petkaAsm.some((a) => a.id === "pm-201-000")) {
    throw new Error("pm-201-000 should be baked into pm-fuel");
  }
  const oilCooling = petkaAsm.find((a) => a.id === "pm-360-000");
  if (
    !oilCooling ||
    !String(oilCooling.glbRel || "").includes("merged/oil-cooling.glb")
  ) {
    throw new Error("pm-360-000 missing merged/oil-cooling.glb");
  }
  if (petkaAsm.some((a) => a.id === "pm-104-005")) {
    throw new Error("pm-104-005 should be baked into pm-360-000");
  }
  if (petkaAsm.some((a) => a.id === "pm-105-005")) {
    throw new Error("pm-105-005 should be baked into pm-360-000");
  }
  for (const id of [
    "pm-010-000",
    "pm-105-020",
    "pm-105-005",
    "pm-201-000",
    "pm-107-010",
    "pm-202-000",
    "pm-202-005",
    "pm-402-000",
    "pm-402-000-mirror",
    "pm-99134104301",
    "pm-99134104301-mirror",
    "pm-501-000",
    "pm-501-001",
    "pm-501-003",
    "pm-501-000-mirror",
    "pm-501-005",
    "pm-501-005-mirror",
    "pm-502-000",
    "pm-502-000-mirror",
  ]) {
    if (petkaAsm.some((a) => a.id === id)) {
      throw new Error(`${id} should be baked into merged layer`);
    }
  }
  {
    const cms = seed.assemblies.filter((a) =>
      String(a.glbRel || "").includes("cms-rip/"),
    );
    if (
      cms.length !== 1 ||
      cms[0].id !== "engine" ||
      !String(cms[0].glbRel).includes("engine_b61_porsche/engine.glb")
    ) {
      throw new Error("locator may only include CMS MA1.03 engine.glb");
    }
  }
  const power = petkaAsm.filter((a) => a.xrayGroup === "power");
  const drive = petkaAsm.filter((a) => a.xrayGroup === "drive");
  if (power.length !== 5 || drive.length !== 6) {
    throw new Error(`expect petka power=5 drive=6, got ${power.length}/${drive.length}`);
  }

  let threw = false;
  try {
    _test.absForRel("../etc/passwd");
  } catch {
    threw = true;
  }
  if (!threw) throw new Error("path escape expected");

  threw = false;
  try {
    _test.absForRel("other/foo.glb");
  } catch {
    threw = true;
  }
  if (!threw) throw new Error("rel root expected");

  const scene = listXrayAssemblies();
  if (!scene.assemblies.length) throw new Error("no assemblies");
  const layers = listXrayLayers();
  const uniqueGlbs = layers.layers.filter((l) => !l.duplicateGlb).map((l) => l.glbRel);
  if (new Set(uniqueGlbs).size !== uniqueGlbs.length) {
    throw new Error("layer dedupe fail");
  }
  if ((layers.layers?.length ?? 0) !== 12) {
    throw new Error(`xray layers expect 12, got ${layers.layers?.length}`);
  }

  const marker = `selfcheck-${Date.now()}`;
  const after = setXrayTransform(marker, {
    position: [0.1, 0.2, 0.3],
    rotationEuler: [0, 0.5, 0],
    scale: [1, 1, 1.05],
  });
  if (!after.layers[marker]) throw new Error("setTransform missing");
  const n = _test.normalizeTransform(after.layers[marker]);
  if (n.position[0] !== 0.1 || n.scale[2] !== 1.05) {
    throw new Error("normalize mismatch");
  }

  const sampleId = power[0].id;
  const bridge = bridgeForAssembly(sampleId);
  if (!bridge || bridge.hotspotId !== "engine-bay") {
    throw new Error("power group bridge");
  }

  const garage = listGarageXrayLayers();
  if ((garage.layers?.length ?? 0) < 1 && !garage.bodyShell?.present) {
    throw new Error("garage scene empty (run fetch:flat-six-components)");
  }
  const gSeed = _test.loadGarageSeed();
  const petkaLayers = gSeed.assemblies.filter((a) =>
    String(a.glbRel || "").startsWith("petka-models/"),
  );
  if (petkaLayers.length !== 11) {
    throw new Error(`garage expect 11 petka, got ${petkaLayers.map((a) => a.id)}`);
  }
  if (!petkaLayers.some((a) => a.id === "pm-cooling")) {
    throw new Error("garage missing pm-cooling");
  }
  if (!petkaLayers.some((a) => a.id === "pm-suspension")) {
    throw new Error("garage missing pm-suspension");
  }
  for (const id of [
    "pm-engine",
    "pm-intake",
    "pm-fuel",
    "pm-302-000",
    "pm-360-000",
    "pm-807-000",
    "pm-807-005",
    "pm-201-020",
    "pm-604-000",
    "pm-suspension",
  ]) {
    if (!petkaLayers.some((a) => a.id === id)) {
      throw new Error(`garage missing ${id}`);
    }
  }
  if (petkaLayers.some((a) => a.id === "pm-801-020")) {
    throw new Error("garage pm-801-020 should be baked into pm-suspension");
  }
  if (petkaLayers.some((a) => a.id === "pm-201-000")) {
    throw new Error("garage pm-201-000 should be baked into pm-fuel");
  }
  if (petkaLayers.some((a) => a.id === "pm-104-005")) {
    throw new Error("garage pm-104-005 should be baked into pm-360-000");
  }
  if (petkaLayers.some((a) => a.id === "pm-105-005")) {
    throw new Error("garage pm-105-005 should be baked into pm-360-000");
  }
  if (petkaLayers.some((a) => a.id === "pm-105-020")) {
    throw new Error("garage pm-105-020 should be baked into pm-intake");
  }
  {
    const cms = gSeed.assemblies.filter((a) =>
      String(a.glbRel || "").includes("cms-rip/"),
    );
    if (cms.length !== 0) {
      throw new Error("garage must not include cms-rip layers");
    }
  }
  if (gSeed.assemblies.some((a) => String(a.glbRel || "").includes("porsche991_chassis"))) {
    throw new Error("garage must not use CMS chassis");
  }
  if (gSeed.assemblies.some((a) => String(a.glbRel || "").includes("porsche991_body"))) {
    throw new Error("garage must not use CMS body");
  }
  if (_test.loadGarageFlows().length < 4) {
    throw new Error("garage flows missing");
  }
  if (_test.loadGarageFlows().some((f) => f.id === "coolant")) {
    throw new Error("coolant flow should be removed (pm-cooling owns lines)");
  }
  if (_test.loadGarageFlows().some((f) => f.id === "oil-lines")) {
    throw new Error("oil-lines flow should be removed (pm-360-000 owns oil cooling)");
  }
  if (_test.loadGarageFlows().some((f) => f.id === "fuel")) {
    throw new Error("fuel flow should be removed (pm-fuel owns fuel tank/lines)");
  }
  if (_test.loadGarageFlows().some((f) => f.id === "brake-lines")) {
    throw new Error("brake-lines flow should be removed (pm-604-000 owns brake system)");
  }
  {
    const cooling = gSeed.assemblies.find((a) => a.id === "pm-cooling");
    if (cooling?.garageStructure !== "lines") {
      throw new Error("pm-cooling should be garageStructure=lines");
    }
    const oilCool = gSeed.assemblies.find((a) => a.id === "pm-360-000");
    if (oilCool?.garageStructure !== "lines") {
      throw new Error("pm-360-000 should be garageStructure=lines");
    }
    if (oilCool?.label_zh !== "机油冷却系统") {
      throw new Error("pm-360-000 should be labeled 机油冷却系统");
    }
    if (!String(oilCool?.glbRel || "").includes("merged/oil-cooling.glb")) {
      throw new Error("pm-360-000 should use merged/oil-cooling.glb");
    }
    const fuel = gSeed.assemblies.find((a) => a.id === "pm-fuel");
    if (fuel?.label_zh !== "燃油系统") {
      throw new Error("pm-fuel should be labeled 燃油系统");
    }
    for (const id of ["pm-807-000", "pm-807-005"]) {
      const a = gSeed.assemblies.find((x) => x.id === id);
      if (a?.garageStructure) {
        throw new Error(`${id} should use interiorZone not garageStructure`);
      }
      if (a?.interiorZone !== "liner") {
        throw new Error(`${id} should be interiorZone=liner (内衬)`);
      }
    }
    const frontTrunk = gSeed.assemblies.find((a) => a.id === "pm-807-000");
    if (frontTrunk?.label_zh !== "前备箱内饰") {
      throw new Error("pm-807-000 should be labeled 前备箱内饰");
    }
    const rearTrunk = gSeed.assemblies.find((a) => a.id === "pm-807-005");
    if (rearTrunk?.label_zh !== "后备箱内饰") {
      throw new Error("pm-807-005 should be labeled 后备箱内饰");
    }
    const intakeAir = gSeed.assemblies.find((a) => a.id === "pm-intake");
    if (intakeAir?.garageStructure !== "air") {
      throw new Error("pm-intake should be garageStructure=air (进排气)");
    }
    if (intakeAir?.label_zh !== "进气系统") {
      throw new Error("pm-intake should be labeled 进气系统");
    }
    const canister = gSeed.assemblies.find((a) => a.id === "pm-201-020");
    if (canister?.garageStructure !== "air") {
      throw new Error("pm-201-020 should be garageStructure=air (进排气)");
    }
    if (canister?.label_zh !== "活性碳罐") {
      throw new Error("pm-201-020 should be labeled 活性碳罐");
    }
    if (!String(canister?.glbRel || "").includes("petka-models/201-020.glb")) {
      throw new Error("pm-201-020 should use petka-models/201-020.glb");
    }
    const fuelAir = gSeed.assemblies.find((a) => a.id === "pm-fuel");
    if (fuelAir?.garageStructure !== "air") {
      throw new Error("pm-fuel should be garageStructure=air");
    }
    const brake = gSeed.assemblies.find((a) => a.id === "pm-604-000");
    if (brake?.garageStructure !== "lines") {
      throw new Error("pm-604-000 should be garageStructure=lines (管路)");
    }
    if (brake?.label_zh !== "制动系统") {
      throw new Error("pm-604-000 should be labeled 制动系统");
    }
  }
  if (Number(gSeed.axle?.frontZ) !== 1.167) {
    throw new Error("garage axle should keep repo CMS frontZ");
  }

  if (meshStateAssemblyId(sampleId) !== sampleId) {
    throw new Error("petka mesh state id should be self");
  }
  if (layerTransformAssemblyId("pm-cooling") !== "pm-cooling") {
    throw new Error("cooling layer transform id");
  }
  const coolingAbs = path.join(root, ".local", "petka-models", "merged", "cooling.glb");
  if (!fs.existsSync(coolingAbs)) {
    throw new Error("run: node scripts/merge-cooling-system.mjs");
  }
  const fuelAbs = path.join(root, ".local", "petka-models", "merged", "fuel.glb");
  if (!fs.existsSync(fuelAbs)) {
    throw new Error("run: npm run merge:fuel");
  }
  const engAbs = path.join(root, ".local", "petka-models", "merged", "engine.glb");
  if (!fs.existsSync(engAbs)) {
    throw new Error("run: npm run merge:engine");
  }
  const cmsEngAbs = path.join(
    root,
    ".local",
    "cms-rip",
    "engine_b61_porsche",
    "engine.glb",
  );
  if (!fs.existsSync(cmsEngAbs)) {
    throw new Error("missing CMS MA1.03 engine.glb (run cms rip export)");
  }
  const drivelineAbs = path.join(
    root,
    ".local",
    "petka-models",
    "merged",
    "driveline.glb",
  );
  if (!fs.existsSync(drivelineAbs)) {
    throw new Error("run: npm run merge:driveline");
  }
  const intakeAbs = path.join(root, ".local", "petka-models", "merged", "intake.glb");
  if (!fs.existsSync(intakeAbs)) {
    throw new Error("run: npm run merge:intake");
  }
  const oilCoolAbs = path.join(
    root,
    ".local",
    "petka-models",
    "merged",
    "oil-cooling.glb",
  );
  if (!fs.existsSync(oilCoolAbs)) {
    throw new Error("run: npm run merge:oil-cooling");
  }
  for (const f of ["010-000.glb", "104-005.glb", "105-005.glb", "105-020.glb", "107-010.glb", "201-000.glb", "201-020.glb", "302-000.glb", "360-000.glb", "501-005.glb", "402-000.glb", "403-006.glb", "604-000.glb", "801-020.glb", "807-000.glb", "807-005.glb", "99134104301.glb"]) {
    const abs = path.join(root, ".local", "petka-models", f);
    if (!fs.existsSync(abs)) throw new Error(`missing petka-models/${f}`);
  }
  const intakeSrc = path.join(root, ".local", "petka-models", "105-020.glb");
  if (
    !glbJsonHas(intakeSrc, '"name":"tripo_part_0"') ||
    glbJsonHas(intakeSrc, '"name":"tripo_part_10"')
  ) {
    throw new Error(
      "105-020.glb is not intake (wrong PETKA export); git checkout e33b58d -- .local/petka-models/105-020.glb && npm run merge:intake",
    );
  }
  const suspAbs = path.join(root, ".local", "petka-models", "merged", "suspension.glb");
  if (!fs.existsSync(suspAbs)) {
    throw new Error("run: node scripts/merge-suspension.mjs");
  }
  const m501Abs = path.join(root, ".local", "petka-models", "merged", "501-000.glb");
  if (!fs.existsSync(m501Abs)) {
    throw new Error("run: npm run merge:501-000 (suspension depends on it)");
  }

  const meshMarker = `selfcheck-mesh-${Date.now()}`;
  setXrayMeshEntry(sampleId, meshMarker, {
    visible: false,
    transform: {
      position: [0.01, 0, 0],
      rotationEuler: [0, 0, 0],
      scale: [1, 1, 1],
    },
  });
  setXrayLayerVisible(sampleId, false);
  const ms = loadXrayMeshState();
  if (ms.layers[sampleId]?.visible !== false) throw new Error("layer visible");
  if (ms.layers[sampleId]?.meshes[meshMarker]?.visible !== false) {
    throw new Error("mesh visible");
  }
  if (ms.layers[sampleId]?.meshes[meshMarker]?.transform?.position[0] !== 0.01) {
    throw new Error("mesh transform");
  }

  if (fs.existsSync(backup)) {
    fs.copyFileSync(backup, tfPath);
    fs.unlinkSync(backup);
  } else if (fs.existsSync(tfPath)) {
    const cur = loadXrayTransforms();
    delete cur.layers[marker];
    fs.writeFileSync(tfPath, JSON.stringify(cur, null, 2) + "\n", "utf8");
    if (Object.keys(cur.layers).length === 0) {
      fs.unlinkSync(tfPath);
    }
  }

  if (fs.existsSync(meshBackup)) {
    fs.copyFileSync(meshBackup, meshPath);
    fs.unlinkSync(meshBackup);
  } else if (fs.existsSync(meshPath)) {
    const cur = loadXrayMeshState();
    if (cur.layers[sampleId]?.meshes?.[meshMarker]) {
      delete cur.layers[sampleId].meshes[meshMarker];
    }
    if (cur.layers[sampleId]) cur.layers[sampleId].visible = true;
    fs.writeFileSync(meshPath, JSON.stringify(cur, null, 2) + "\n", "utf8");
  }

  console.log(
    `XRAY ASSETS SELFCHECK PASS; layers=${layers.layers.map((l) => l.id).join(",") || "none"}; present=${scene.assemblies.filter((a) => a.present).map((a) => a.id).join(",") || "none"}`,
  );
} catch (e) {
  if (fs.existsSync(backup)) {
    fs.copyFileSync(backup, tfPath);
    fs.unlinkSync(backup);
  }
  if (fs.existsSync(meshBackup)) {
    fs.copyFileSync(meshBackup, meshPath);
    fs.unlinkSync(meshBackup);
  }
  console.error(e);
  process.exit(1);
}
