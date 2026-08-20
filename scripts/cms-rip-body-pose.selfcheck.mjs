/**
 * Assert CMS 991 body.glb has baked Unity poses (not piled at origin).
 * Body scene is -Z forward (bumper_front Z < 0). Doors keep Unity L/R.
 * Run: node scripts/cms-rip-body-pose.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const glb = path.join(root, ".local/cms-rip/porsche991_body/body.glb");
const exportPy = path.join(root, "scripts/cms_rip_export.py");

if (!fs.existsSync(glb)) {
  throw new Error("missing body.glb — run npm run export:cms-rip:body");
}
const src = fs.readFileSync(exportPy, "utf8");
for (const needle of [
  "collect_body_pose_matrices",
  "export_body_car_frame",
  "--body-only",
  "-Z forward",
]) {
  if (!src.includes(needle)) {
    throw new Error(`cms_rip_export.py missing ${needle}`);
  }
}
if (src.includes("Y180 → +Z forward")) {
  throw new Error("body must not apply Y180 car-frame flip (mirrors L/R + lids)");
}

const buf = fs.readFileSync(glb);
const jsonLen = buf.readUInt32LE(12);
const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
const meshes = json.meshes || [];
if (meshes.length < 20) throw new Error(`body_mesh_count:${meshes.length}`);

function centerOf(name) {
  const m = meshes.find((x) => x.name === name);
  if (!m) throw new Error(`missing_mesh:${name}`);
  const acc = json.accessors[m.primitives[0].attributes.POSITION];
  return acc.min.map((v, i) => (v + acc.max[i]) / 2);
}

const bf = centerOf("bumper_front");
const br = centerOf("bumper_rear");
const dl = centerOf("door_front_left");
const dr = centerOf("door_front_right");
const hl = centerOf("headlight_left");
const tl = centerOf("taillight_left");
const hood = centerOf("hood");

// Unity native: -Z forward
if (!(bf[2] < -1.0 && br[2] > 1.0)) {
  throw new Error(`bumper_z_not_unity_native front=${bf[2]} rear=${br[2]}`);
}
// left door on +X when -Z forward (Unity)
if (!(dl[0] > 0.5 && dr[0] < -0.5)) {
  throw new Error(`doors_L/R_wrong leftX=${dl[0]} rightX=${dr[0]}`);
}
if (!(hl[2] < -1.0 && tl[2] > 1.0)) {
  throw new Error(`lights_z_wrong hl=${hl[2]} tl=${tl[2]}`);
}
// hood in CMS sits with rear bumper (rear deck / engine lid)
if (!(hood[2] > 1.0)) {
  throw new Error(`hood_should_be_rear z=${hood[2]}`);
}

console.log(
  `cms-rip-body-pose.selfcheck: OK meshes=${meshes.length} bumperZ=${bf[2].toFixed(2)}/${br[2].toFixed(2)} doorX=${dl[0].toFixed(2)}/${dr[0].toFixed(2)} hoodZ=${hood[2].toFixed(2)}`,
);
