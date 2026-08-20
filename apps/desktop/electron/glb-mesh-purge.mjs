/**
 * Remove named meshes from a GLB (JSON graph only; BIN may keep orphans).
 * ponytail: no buffer rewrite — viewer ignores unreferenced accessors.
 */
import fs from "node:fs";

export function readGlb(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.toString("utf8", 0, 4) !== "glTF") throw new Error("not_glb");
  let off = 12;
  /** @type {{ type: string, data: Buffer }[]} */
  const chunks = [];
  while (off + 8 <= buf.length) {
    const len = buf.readUInt32LE(off);
    const type = buf.toString("utf8", off + 4, off + 8);
    off += 8;
    chunks.push({ type, data: buf.subarray(off, off + len) });
    off += len;
  }
  const jsonChunk = chunks.find((c) => c.type.startsWith("JSON"));
  const binChunk = chunks.find((c) => c.type.startsWith("BIN"));
  if (!jsonChunk) throw new Error("glb_no_json");
  const json = JSON.parse(jsonChunk.data.toString("utf8"));
  return { json, bin: binChunk ? Buffer.from(binChunk.data) : Buffer.alloc(0) };
}

export function writeGlb(json, bin) {
  let jsonStr = JSON.stringify(json);
  while (Buffer.byteLength(jsonStr) % 4) jsonStr += " ";
  const jsonBuf = Buffer.from(jsonStr, "utf8");
  const binPad = (4 - (bin.length % 4)) % 4;
  const binBuf = binPad ? Buffer.concat([bin, Buffer.alloc(binPad)]) : bin;
  const total = 12 + 8 + jsonBuf.length + 8 + binBuf.length;
  const out = Buffer.alloc(total);
  out.write("glTF", 0);
  out.writeUInt32LE(2, 4);
  out.writeUInt32LE(total, 8);
  let o = 12;
  out.writeUInt32LE(jsonBuf.length, o);
  out.write("JSON", o + 4);
  jsonBuf.copy(out, o + 8);
  o += 8 + jsonBuf.length;
  out.writeUInt32LE(binBuf.length, o);
  out.write("BIN\0", o + 4);
  binBuf.copy(out, o + 8);
  return out;
}

/**
 * @param {object} json glTF JSON
 * @param {Set<string>|string[]} removeNames mesh/node names to drop
 * @returns {{ removed: string[], remainingMeshes: number }}
 */
export function purgeMeshesFromGlbJson(json, removeNames) {
  const want = new Set(
    [...removeNames].map((n) => String(n || "").trim()).filter(Boolean),
  );
  if (!want.size) return { removed: [], remainingMeshes: json.meshes?.length ?? 0 };

  const nodes = Array.isArray(json.nodes) ? json.nodes : [];
  const meshes = Array.isArray(json.meshes) ? json.meshes : [];

  /** @type {Set<number>} */
  const dropMeshIdx = new Set();
  /** @type {string[]} */
  const removed = [];

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const nName = node?.name ? String(node.name) : "";
    const meshIdx = typeof node?.mesh === "number" ? node.mesh : -1;
    const mName =
      meshIdx >= 0 && meshes[meshIdx]?.name
        ? String(meshes[meshIdx].name)
        : "";
    if (want.has(nName) || (mName && want.has(mName))) {
      if (meshIdx >= 0) dropMeshIdx.add(meshIdx);
      if (nName) removed.push(nName);
      else if (mName) removed.push(mName);
    }
  }
  for (let mi = 0; mi < meshes.length; mi++) {
    const mName = meshes[mi]?.name ? String(meshes[mi].name) : "";
    if (mName && want.has(mName)) {
      dropMeshIdx.add(mi);
      removed.push(mName);
    }
  }

  // Also drop by exact want names even if only mesh index matched via node
  for (const mi of dropMeshIdx) {
    const mName = meshes[mi]?.name ? String(meshes[mi].name) : `mesh_${mi}`;
    removed.push(mName);
  }

  if (!dropMeshIdx.size) {
    return { removed: [], remainingMeshes: meshes.length };
  }

  // Remap mesh indices: old -> new (or -1 if dropped)
  const meshRemap = new Map();
  const newMeshes = [];
  for (let i = 0; i < meshes.length; i++) {
    if (dropMeshIdx.has(i)) {
      meshRemap.set(i, -1);
      continue;
    }
    meshRemap.set(i, newMeshes.length);
    newMeshes.push(meshes[i]);
  }
  json.meshes = newMeshes;

  // Rebuild nodes: drop nodes whose mesh was removed; remap remaining
  const nodeRemap = new Map();
  const newNodes = [];
  for (let i = 0; i < nodes.length; i++) {
    const node = { ...nodes[i] };
    if (typeof node.mesh === "number") {
      const mapped = meshRemap.get(node.mesh);
      if (mapped === undefined || mapped < 0) {
        nodeRemap.set(i, -1);
        continue;
      }
      node.mesh = mapped;
    }
    // fix children indices later
    nodeRemap.set(i, newNodes.length);
    newNodes.push(node);
  }

  for (const node of newNodes) {
    if (!Array.isArray(node.children)) continue;
    node.children = node.children
      .map((c) => nodeRemap.get(c))
      .filter((c) => typeof c === "number" && c >= 0);
    if (!node.children.length) delete node.children;
  }
  json.nodes = newNodes;

  if (Array.isArray(json.scenes)) {
    for (const sc of json.scenes) {
      if (!Array.isArray(sc.nodes)) continue;
      sc.nodes = sc.nodes
        .map((c) => nodeRemap.get(c))
        .filter((c) => typeof c === "number" && c >= 0);
    }
  }

  // Drop skins/cameras refs that pointed at removed nodes — best-effort skip

  return {
    removed: [...new Set(removed)],
    remainingMeshes: newMeshes.length,
  };
}

/**
 * @param {string} glbPath
 * @param {Set<string>|string[]} removeNames
 */
export function purgeMeshesFromGlbFile(glbPath, removeNames) {
  if (!fs.existsSync(glbPath)) throw new Error(`glb_missing:${glbPath}`);
  const { json, bin } = readGlb(glbPath);
  const before = json.meshes?.length ?? 0;
  const result = purgeMeshesFromGlbJson(json, removeNames);
  if (!result.removed.length && before === result.remainingMeshes) {
    // try again: names might only be in want but matched via mesh index path already empty
    const want = new Set([...removeNames].map(String));
    const still = (json.nodes || []).some(
      (n) => n?.name && want.has(String(n.name)),
    );
    if (!still) {
      return { ...result, bytes: fs.statSync(glbPath).size, changed: false };
    }
  }
  const bak = glbPath + ".pre-purge.bak";
  if (!fs.existsSync(bak)) fs.copyFileSync(glbPath, bak);
  const out = writeGlb(json, bin);
  fs.writeFileSync(glbPath, out);
  return {
    ...result,
    bytes: out.length,
    changed: true,
    remainingMeshes: json.meshes?.length ?? 0,
  };
}
