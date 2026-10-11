/** Explicit local part grouping/splitting; original transforms, materials and texture bytes stay intact. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { Matrix4, Quaternion, Vector3 } from 'three';

const args = process.argv.slice(2);
const glbPath = path.resolve(args.includes('--glb') ? args[args.indexOf('--glb') + 1] : '.local/garage-models/boxster-primary.glb');
const manifestPath = glbPath.replace(/\.glb$/i, '.manifest.json');
const original = fs.readFileSync(glbPath);
assert.equal(original.toString('ascii', 0, 4), 'glTF');
const jsonLength = original.readUInt32LE(12);
const gltf = JSON.parse(original.subarray(20, 20 + jsonLength).toString('utf8'));
const binOffset = 20 + jsonLength;
const originalBin = original.subarray(binOffset + 8, binOffset + 8 + original.readUInt32LE(binOffset));
assert.equal(gltf.buffers.length, 1);
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const additions = [], summary = { merged: [], split: [], removed: [], reassigned: [], mirrored: [] };
let binLength = originalBin.length;
const merges = {
  'Exterior__other__左后轮胎': ['W:Object_179TopLevelGroup_c4bb8b1e_dds1_0', 'W:TopLevelGroupObject_182__c5ebe6d_dds_0'],
  'Exterior__other__右后轮胎': ['Object_179TopLevelGroup_c4bb8b1e_dds1_0', 'TopLevelGroupObject_182__c5ebe6d_dds_0'],
  'Exterior__other__右前轮胎': ['W1_c4bb8b1e_dds1_0', 'W3__c5ebe6d_dds_0'],
  'Exterior__other__左前轮胎': ['W1_c4bb8b1e_dds1_0.001', 'W3__c5ebe6d_dds_0.001'],
  'Exterior__other__718__GEO_Chassis_SUB1_Part_002_010_012': [
    '718__GEO_Chassis_SUB1_Part_002', '718__GEO_Chassis_SUB1_Part_010', '718__GEO_Chassis_SUB1_Part_012',
  ],
  'Exterior__other__718__GEO_Chassis_SUB1_Part_003_011_013': [
    '718__GEO_Chassis_SUB1_Part_003', '718__GEO_Chassis_SUB1_Part_011', '718__GEO_Chassis_SUB1_Part_013',
  ],
  'Cabin__dashboard__steeringWheel__005_070__assembly': [
    'steeringWheel__005_070__assembly', 'steeringWheel__interior_071',
  ],
  'Exterior__rear-bumper__SM_RearKit_0000003_MAT_Details_MAT130_0__rear_diffuser_assembly': [
    'SM_RearKit_0000.003_MAT_Details_MAT.130_0__rear_diffuser_L_shell',
    'SM_RearKit_0000.003_MAT_Details_MAT.130_0__exhaust_surround',
    'SM_RearKit_0000.003_MAT_Details_MAT.130_0__rear_diffuser_R_shell',
  ],
};

function parentList(index) {
  const parent = gltf.nodes.find(node => node.children?.includes(index));
  if (parent) return parent.children;
  const scene = gltf.scenes.find(scene => scene.nodes.includes(index));
  assert.ok(scene, 'Node must have a parent or scene');
  return scene.nodes;
}
function mergeParts(name, sources, byName = false) {
  if (gltf.nodes.some(n => n.extras?.garagePartName === name)) return;
  const indices = sources.map(source => gltf.nodes.findIndex(n => byName ? n.name === source : n.extras?.sourceObject === source));
  if (indices.every(i => i < 0)) return;
  assert.ok(indices.every(i => i >= 0), `Incomplete part group: ${name}`);
  const siblings = parentList(indices[0]);
  assert.ok(indices.every(i => parentList(i) === siblings), 'Merged nodes must share a parent');
  const { garageCategory, garageZone, sourceRole, sourceAsset } = gltf.nodes[indices[0]].extras;
  assert.ok(indices.every(i => gltf.nodes[i].extras.garageCategory === garageCategory &&
    gltf.nodes[i].extras.garageZone === garageZone), 'Merged nodes must share category and zone');
  assert.ok(['cabin', 'exterior'].includes(garageCategory));
  const originals = [...new Set(indices.flatMap(i => gltf.nodes[i].extras.sourceObjects ?? [gltf.nodes[i].extras.sourceObject]))];
  const node = { name, children: indices, extras: { garagePartName: name,
    garageCategory, garageZone, preserveGarageMaterial: true,
    sourceRole, sourceAsset, sourceObjects: originals } };
  const newIndex = gltf.nodes.push(node) - 1;
  siblings.splice(siblings.indexOf(indices[0]), 0, newIndex);
  for (const i of indices) siblings.splice(siblings.indexOf(i), 1);
  manifest.parts = manifest.parts.filter(p => byName ? !sources.includes(p.name) : !sources.includes(p.original));
  manifest.parts.push({ name, originals, ...(byName ? { originalParts: sources } : {}), category: garageCategory === 'cabin' ? 'Cabin' : 'Exterior',
    zone: garageZone, role: sourceRole, source: sourceAsset });
  summary.merged.push({ name, originals, ...(byName ? { originalParts: sources } : {}) });
}
for (const [name, sources] of Object.entries(merges)) mergeParts(name, sources);

const widths = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
const dimensions = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
function accessorData(index) {
  const accessor = gltf.accessors[index], view = gltf.bufferViews[accessor.bufferView];
  assert.ok(!accessor.sparse && view.buffer === 0);
  const width = widths[accessor.componentType], size = width * dimensions[accessor.type];
  const stride = view.byteStride ?? size;
  const start = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
  const bytes = Buffer.alloc(accessor.count * size);
  for (let i = 0; i < accessor.count; i++) originalBin.copy(bytes, i * size, start + i * stride, start + i * stride + size);
  return { accessor, bytes, width, size };
}
function appendAccessor(bytes, definition, target) {
  const padding = (4 - binLength % 4) % 4;
  if (padding) { additions.push(Buffer.alloc(padding)); binLength += padding; }
  const view = gltf.bufferViews.push({ buffer: 0, byteOffset: binLength, byteLength: bytes.length, target }) - 1;
  additions.push(bytes); binLength += bytes.length;
  return gltf.accessors.push({ ...definition, bufferView: view, byteOffset: 0 }) - 1;
}
const splits = [
  { source: 'SM_FrontKit_0000.003_MAT_Light.030_0', sides: !args.includes('--loose') },
  { source: 'SM_RearKit_0000.003_MAT_Details_MAT.130_0__right_side_details', sides: false },
  { source: 'SM_RearKit_0000.003_MAT_Details_MAT.130_0__left_side_details', sides: false },
  { source: 'dashboard__interior_041__01', sides: false },
  { source: 'steeringWheel__interior_072', sides: false },
];
function connectedGeometry(splitIndex) {
  const node = gltf.nodes[splitIndex], mesh = gltf.meshes[node.mesh];
  assert.equal(mesh.primitives.length, 1, 'Expected one source material');
  const primitive = mesh.primitives[0];
  assert.ok((primitive.mode ?? 4) === 4 && !primitive.targets && !node.skin);
  const position = accessorData(primitive.attributes.POSITION);
  assert.equal(position.accessor.componentType, 5126);
  const indices = primitive.indices == null ? Array.from({ length: position.accessor.count }, (_, i) => i)
    : (() => { const { bytes, width } = accessorData(primitive.indices); return Array.from({ length: bytes.length / width }, (_, i) => bytes.readUIntLE(i * width, width)); })();
  const vertex = i => [0, 1, 2].map(c => position.bytes.readFloatLE(i * 12 + c * 4));
  const parent = Array.from({ length: position.accessor.count }, (_, i) => i);
  function find(i) { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; }
  const join = (a, b) => { parent[find(a)] = find(b); };
  // glTF UV/normal seams duplicate vertices; weld only for connectivity discovery.
  const shared = new Map();
  for (const i of new Set(indices)) {
    const key = vertex(i).map(v => Math.round(v * 1e6)).join(',');
    if (shared.has(key)) join(i, shared.get(key)); else shared.set(key, i);
  }
  for (let t = 0; t < indices.length; t += 3) { join(indices[t], indices[t + 1]); join(indices[t], indices[t + 2]); }
  const components = new Map();
  for (let t = 0; t < indices.length; t += 3) {
    const key = find(indices[t]);
    if (!components.has(key)) components.set(key, []);
    components.get(key).push(...indices.slice(t, t + 3));
  }
  const matrix = worldMatrix(splitIndex);
  const groups = [...components.values()].map(indices => {
    const vertices = [...new Set(indices)];
    const center = new Vector3();
    for (const i of vertices) center.add(new Vector3().fromArray(vertex(i)).applyMatrix4(matrix));
    return { indices, vertices, center: center.divideScalar(vertices.length).toArray() };
  }).sort((a, b) => b.center[0] - a.center[0]);
  return { node, mesh, primitive, groups, vertex };
}
function appendSubset(primitive, group, vertex) {
  const remap = new Map(group.vertices.map((v, i) => [v, i]));
  const attributes = {};
  for (const [semantic, index] of Object.entries(primitive.attributes)) {
    const { accessor, bytes, size } = accessorData(index);
    const subset = Buffer.concat(group.vertices.map(v => bytes.subarray(v * size, (v + 1) * size)));
    const { bufferView, byteOffset, min, max, ...definition } = accessor;
    definition.count = group.vertices.length;
    if (semantic === 'POSITION') {
      definition.min = [0, 1, 2].map(c => Math.min(...group.vertices.map(i => vertex(i)[c])));
      definition.max = [0, 1, 2].map(c => Math.max(...group.vertices.map(i => vertex(i)[c])));
    }
    attributes[semantic] = appendAccessor(subset, definition, 34962);
  }
  const indexBytes = Buffer.alloc(group.indices.length * 4);
  group.indices.forEach((i, n) => indexBytes.writeUInt32LE(remap.get(i), n * 4));
  return { ...primitive, attributes, indices: appendAccessor(indexBytes,
    { componentType: 5125, type: 'SCALAR', count: group.indices.length }, 34963) };
}
function splitPart(splitSource, sides) {
  const splitIndex = gltf.nodes.findIndex(n => n.mesh != null && n.extras?.sourceObject === splitSource && !n.extras?.garageSplitPart);
  if (splitIndex < 0) return;
  const { node, mesh, primitive, groups: originalGroups, vertex } = connectedGeometry(splitIndex);
  let groups = originalGroups;
  if (sides) {
    groups = [true, false].map(left => {
      const members = groups.filter(group => (group.center[0] > 0) === left);
      return { indices: members.flatMap(group => group.indices),
        vertices: [...new Set(members.flatMap(group => group.vertices))],
        center: members.reduce((sum, group) => sum.map((v, c) => v + group.center[c] / members.length), [0, 0, 0]) };
    }).filter(group => group.indices.length);
  }
  assert.ok(groups.length > 1, 'Model has no disconnected pieces to split');
  const replacements = [];
  for (const [part, group] of groups.entries()) {
    const name = `${node.name}__${sides && groups.length === 2 ? (part === 0 ? '左' : '右') : `分块${String(part + 1).padStart(2, '0')}`}`;
    const nextPrimitive = appendSubset(primitive, group, vertex);
    const nextMesh = gltf.meshes.push({ ...mesh, name, primitives: [nextPrimitive] }) - 1;
    const nextNode = { ...node, name, mesh: nextMesh, extras: { ...node.extras, garageSplitPart: part + 1 } };
    replacements.push(gltf.nodes.push(nextNode) - 1);
    const record = manifest.parts.find(p => p.original === splitSource);
    manifest.parts.push({ ...record, name, splitPart: part + 1 });
    summary.split.push({ name, original: splitSource, triangles: group.indices.length / 3, center: group.center });
  }
  const siblings = parentList(splitIndex);
  siblings.splice(siblings.indexOf(splitIndex), 1, ...replacements);
  manifest.parts = manifest.parts.filter(p => p.original !== splitSource || p.splitPart);
  // Keep the old buffer/mesh unreferenced; all other binary data is byte-for-byte retained.
  gltf.nodes[splitIndex] = { name: `${node.name}__split-source`, extras: { garageSplitSource: splitSource } };
}
for (const { source, sides } of splits) splitPart(source, sides);
mergeParts('Cabin__dashboard__dashboard__interior_041__01__分块03_04_05',
  ['03', '04', '05'].map(part => `Cabin__dashboard__dashboard__interior_041__01__分块${part}`), true);

// The small centre ring belongs to the airbag face, rather than the wheel rim/spokes.
const wheelSource = 'steeringWheel__005_070__assembly';
const airbagName = 'Cabin__dashboard__steeringWheel__interior_004__01';
const wheelIndex = gltf.nodes.findIndex(n => n.mesh != null && n.extras?.sourceObject === wheelSource);
if (wheelIndex >= 0 && !gltf.nodes[wheelIndex].extras.garageCentreRingReassigned) {
  const { node, mesh, primitive, groups, vertex } = connectedGeometry(wheelIndex);
  const rings = groups.filter(group => {
    const positions = group.vertices.map(vertex);
    const min = [0, 1, 2].map(c => Math.min(...positions.map(p => p[c])));
    const max = [0, 1, 2].map(c => Math.max(...positions.map(p => p[c])));
    return max[0] - min[0] < 0.1 && max[1] - min[1] < 0.1 && min[1] > -0.01 && max[1] < 0.1;
  });
  assert.equal(rings.length, 1, 'Expected exactly one centre ring');
  const ring = rings[0];
  assert.equal(ring.indices.length / 3, 6144, 'Centre ring source geometry changed');
  const remainingIndices = groups.filter(g => g !== ring).flatMap(g => g.indices);
  const remaining = { indices: remainingIndices, vertices: [...new Set(remainingIndices)] };
  node.mesh = gltf.meshes.push({ ...mesh, primitives: [appendSubset(primitive, remaining, vertex)] }) - 1;
  node.extras.garageCentreRingReassigned = airbagName;
  const ringName = 'Cabin__dashboard__steeringWheel__centerRing__to_interior_004__01';
  const ringMesh = gltf.meshes.push({ ...mesh, name: ringName, primitives: [appendSubset(primitive, ring, vertex)] }) - 1;
  const ringNode = { ...node, name: ringName, mesh: ringMesh, extras: { ...node.extras,
    sourceObject: 'steeringWheel__centerRing__to_interior_004__01', sourceObjects: [wheelSource],
    garageTransferredFrom: wheelSource, garageTransferredTo: airbagName } };
  const airbagIndex = gltf.nodes.findIndex(n => n.name === airbagName);
  assert.ok(airbagIndex >= 0, 'Airbag destination is missing');
  parentList(airbagIndex).push(gltf.nodes.push(ringNode) - 1);
  manifest.parts.push({ name: ringName, original: ringNode.extras.sourceObject, category: 'Cabin',
    zone: 'dashboard', role: node.extras.sourceRole, source: node.extras.sourceAsset });
  mergeParts(airbagName, [airbagName, ringName], true);
  summary.reassigned.push({ from: wheelSource, to: airbagName, triangles: ring.indices.length / 3 });
}

// Explicit right-side copy, reflected across the vehicle centre plane in world space.
const mirrorName = 'Cabin__seats__rightSeat__interior_009__01__mirrored_from_left';
if (!gltf.nodes.some(n => n.name === mirrorName)) {
  const from = 'leftSeat__interior_009__01';
  const index = gltf.nodes.findIndex(n => n.mesh != null && n.extras?.sourceObject === from);
  if (index >= 0) {
    const source = gltf.nodes[index];
    assert.ok(!source.children?.length, 'Mirror source must be one leaf mesh');
    const parent = gltf.nodes.findIndex(n => n.children?.includes(index));
    const local = (parent < 0 ? new Matrix4() : worldMatrix(parent).invert())
      .multiply(new Matrix4().makeScale(-1, 1, 1)).multiply(worldMatrix(index));
    const position = new Vector3(), rotation = new Quaternion(), scale = new Vector3();
    local.decompose(position, rotation, scale);
    const original = 'rightSeat__interior_009__01__mirrored_from_left';
    const node = { ...source, name: mirrorName, translation: position.toArray(), rotation: rotation.toArray(), scale: scale.toArray(),
      extras: { ...source.extras, sourceObject: original, sourceObjects: [from], garageMirroredFrom: from, garageMirrorPlane: 'vehicle-x=0' } };
    delete node.matrix;
    parentList(index).push(gltf.nodes.push(node) - 1);
    manifest.parts.push({ name: mirrorName, original, category: 'Cabin', zone: 'seats',
      role: source.extras.sourceRole, source: source.extras.sourceAsset, mirroredFrom: from });
    summary.mirrored.push({ name: mirrorName, from, plane: 'vehicle-x=0' });
  }
}

for (const source of [
  '718__GEO_Stitches_Center_Part_006', '718__GEO_Stitches_Center_Part_003',
  '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_029', '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_114',
  '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_028',
  '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_085',
  'leftDoorTrim__interior_064__corrected__part_011',
  'leftDoorTrim__interior_064__corrected__part_003',
  'centerConsole__interior_009__01__part_001',
  'centerConsole__interior_009__01__part_002',
  'centerConsole__interior_009__01__part_437',
  'centerConsole__interior_009__01__part_438',
  'defrostVent__right__assembly',
  'defrostVent__left__assembly',
  '718__GEO_INT_PLASTIC_BLACK_SUB0_Part_272',
  '718__GEO_INT_LEATHER_SUB1_Part_002',
  '981__rolloverGrille__L',
  '981__rolloverGrille__R',
]) {
  const index = gltf.nodes.findIndex(n => n.mesh != null && n.extras?.sourceObject === source);
  if (index < 0) continue;
  const node = gltf.nodes[index], siblings = parentList(index);
  siblings.splice(siblings.indexOf(index), 1);
  gltf.nodes[index] = { name: `${node.name}__removed-source`, extras: { garageRemovedSource: source } };
  manifest.parts = manifest.parts.filter(p => p.original !== source);
  summary.removed.push({ name: node.name, original: source });
}

// Replay the user's saved desktop exterior joins after the earlier part refinements.
// A join can cross an existing selectable group; retain each child's world matrix.
const exteriorEdits = JSON.parse(fs.readFileSync(new URL('../data/seed/flat-six/garage-exterior-edits.json', import.meta.url)));
const affectedParents = new Set();
function worldMatrix(index) {
  const node = gltf.nodes[index];
  const local = node.matrix ? new Matrix4().fromArray(node.matrix) : new Matrix4().compose(
    new Vector3().fromArray(node.translation ?? [0, 0, 0]),
    new Quaternion().fromArray(node.rotation ?? [0, 0, 0, 1]),
    new Vector3().fromArray(node.scale ?? [1, 1, 1]));
  const parent = gltf.nodes.findIndex(n => n.children?.includes(index));
  return parent < 0 ? local : worldMatrix(parent).multiply(local);
}
for (const name of exteriorEdits.removed) {
  const index = gltf.nodes.findIndex(n => n.mesh != null && n.name === name);
  if (index < 0) continue;
  const node = gltf.nodes[index];
  assert.equal(node.extras.garageCategory, 'exterior');
  const siblings = parentList(index);
  siblings.splice(siblings.indexOf(index), 1);
  gltf.nodes[index] = { name: `${name}__removed-source`, extras: { garageRemovedSource: node.extras.sourceObject } };
  manifest.parts = manifest.parts.filter(p => p.name !== name);
  summary.removed.push({ name, original: node.extras.sourceObject });
}
for (const edit of exteriorEdits.merged) {
  if (gltf.nodes.some(n => n.extras?.garageDesktopEdit === edit.name)) continue;
  const indices = edit.nodes.map(name => gltf.nodes.findIndex(n => n.mesh != null && n.name === name));
  if (indices.every(i => i < 0)) continue;
  assert.ok(indices.every(i => i >= 0), `Incomplete desktop join: ${edit.name}`);
  assert.ok(edit.nodes.includes(edit.anchor), `Desktop join anchor is missing: ${edit.name}`);
  const anchor = indices[edit.nodes.indexOf(edit.anchor)];
  const siblings = parentList(anchor);
  const parent = gltf.nodes.findIndex(n => n.children === siblings);
  const inverseParent = parent < 0 ? new Matrix4() : worldMatrix(parent).invert();
  const worlds = indices.map(worldMatrix);
  const previousParents = indices.map(parentList);
  for (const list of previousParents) {
    const oldParent = gltf.nodes.find(n => n.children === list);
    if (oldParent?.extras?.garagePartName) affectedParents.add(oldParent);
  }
  const extras = gltf.nodes[anchor].extras;
  assert.ok(indices.every(i => gltf.nodes[i].extras.garageCategory === 'exterior'));
  const originals = indices.flatMap(i => gltf.nodes[i].extras.sourceObjects ?? [gltf.nodes[i].extras.sourceObject]);
  const group = { name: edit.name, children: indices, extras: { garagePartName: edit.name,
    garageDesktopEdit: edit.name, garageCategory: 'exterior', garageZone: extras.garageZone,
    preserveGarageMaterial: true, sourceRole: extras.sourceRole, sourceAsset: extras.sourceAsset, sourceObjects: originals } };
  const newIndex = gltf.nodes.push(group) - 1;
  siblings.splice(siblings.indexOf(anchor), 0, newIndex);
  for (const [n, index] of indices.entries()) {
    const previousSiblings = previousParents[n];
    previousSiblings.splice(previousSiblings.indexOf(index), 1);
    const node = gltf.nodes[index];
    delete node.translation; delete node.rotation; delete node.scale;
    node.matrix = inverseParent.clone().multiply(worlds[n]).toArray();
  }
  manifest.parts = manifest.parts.filter(p => !edit.nodes.includes(p.name));
  manifest.parts.push({ name: edit.name, originals, originalParts: edit.nodes, category: 'Exterior',
    zone: extras.garageZone, role: extras.sourceRole, source: extras.sourceAsset });
  summary.merged.push({ name: edit.name, originals, originalParts: edit.nodes, desktopEdit: true });
}
function remainingSources(node) {
  return node.mesh != null ? node.extras.sourceObjects ?? [node.extras.sourceObject]
    : (node.children ?? []).flatMap(i => remainingSources(gltf.nodes[i]));
}
for (const parent of affectedParents) {
  parent.extras.sourceObjects = [...new Set(remainingSources(parent))];
  const part = manifest.parts.find(p => p.name === parent.extras.garagePartName);
  if (part) part.originals = parent.extras.sourceObjects;
}

if (!summary.merged.length && !summary.split.length && !summary.removed.length && !summary.reassigned.length && !summary.mirrored.length) {
  console.log('No pending part refinements');
} else if (!args.includes('--apply')) {
  console.log(JSON.stringify({ dryRun: true, ...summary }, null, 2));
} else {
  const binary = Buffer.concat([originalBin, ...additions]);
  gltf.buffers[0].byteLength = binary.length;
  const json = Buffer.from(JSON.stringify(gltf));
  const jsonPad = Buffer.alloc((4 - json.length % 4) % 4, 0x20);
  const binPad = Buffer.alloc((4 - binary.length % 4) % 4);
  const header = Buffer.alloc(20); header.write('glTF'); header.writeUInt32LE(2, 4);
  header.writeUInt32LE(28 + json.length + jsonPad.length + binary.length + binPad.length, 8);
  header.writeUInt32LE(json.length + jsonPad.length, 12); header.writeUInt32LE(0x4e4f534a, 16);
  const binHeader = Buffer.alloc(8); binHeader.writeUInt32LE(binary.length + binPad.length, 0); binHeader.writeUInt32LE(0x004e4942, 4);
  const pending = glbPath + '.pending';
  fs.writeFileSync(pending, Buffer.concat([header, json, jsonPad, binHeader, binary, binPad]));
  fs.renameSync(pending, glbPath);
  manifest.exportedParts = manifest.parts.length;
  manifest.mergedParts = [...(manifest.mergedParts ?? []), ...summary.merged];
  manifest.splitParts = [...(manifest.splitParts ?? []), ...summary.split];
  manifest.removedParts = [...(manifest.removedParts ?? []), ...summary.removed];
  manifest.reassignedParts = [...(manifest.reassignedParts ?? []), ...summary.reassigned];
  manifest.mirroredParts = [...(manifest.mirroredParts ?? []), ...summary.mirrored];
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(JSON.stringify({ applied: true, exportedParts: manifest.exportedParts, ...summary }, null, 2));
}
