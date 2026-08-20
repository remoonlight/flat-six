/**
 * Selfcheck: glb mesh purge remaps nodes/meshes.
 * Run: node apps/desktop/electron/glb-mesh-purge.selfcheck.mjs
 */
import { purgeMeshesFromGlbJson } from "./glb-mesh-purge.mjs";

const fixture = {
  asset: { version: "2.0" },
  scenes: [{ nodes: [0] }],
  nodes: [
    { name: "root", children: [1, 2] },
    { name: "keep_me", mesh: 0 },
    { name: "drop_me", mesh: 1 },
  ],
  meshes: [{ name: "keep_me" }, { name: "drop_me" }],
};

const r = purgeMeshesFromGlbJson(fixture, ["drop_me"]);
if (!r.removed.includes("drop_me")) throw new Error("not removed");
if (fixture.meshes.length !== 1) throw new Error("mesh count");
if (fixture.meshes[0].name !== "keep_me") throw new Error("wrong kept");
if (fixture.nodes.some((n) => n.name === "drop_me")) {
  throw new Error("node remained");
}
if (fixture.nodes.find((n) => n.name === "keep_me")?.mesh !== 0) {
  throw new Error("remap failed");
}
console.log("glb-mesh-purge.selfcheck: OK", r);
