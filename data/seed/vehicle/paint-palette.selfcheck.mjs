/**
 * Self-check: paint-palette.json covers domain PAINT_OPTIONS / tops / interiors.
 * Run: node data/seed/vehicle/paint-palette.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  INTERIOR_OPTIONS_SPEC,
  PAINT_OPTIONS,
  TOP_OPTIONS_SPEC,
} from "../../../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const palettePath = path.join(__dirname, "paint-palette.json");
const palette = JSON.parse(fs.readFileSync(palettePath, "utf8"));
const paints = palette.paints || {};
const softTops = palette.softTops || {};
const interiors = palette.interiors || {};

const hexRe = /^#[0-9A-Fa-f]{6}$/;

for (const p of PAINT_OPTIONS) {
  const entry = paints[p.en];
  if (!entry?.hex || !hexRe.test(entry.hex)) {
    throw new Error(`paint_missing_or_bad_hex:${p.en}`);
  }
  if (entry.code !== p.code) {
    throw new Error(`paint_code_mismatch:${p.en}:${entry.code}!=${p.code}`);
  }
  if (entry.hex.toUpperCase() !== p.hex.toUpperCase()) {
    throw new Error(`paint_hex_mismatch:${p.en}`);
  }
}

for (const t of TOP_OPTIONS_SPEC) {
  const entry = softTops[t.code];
  if (!entry?.hex || !hexRe.test(entry.hex)) {
    throw new Error(`softTop_missing_or_bad_hex:${t.code}`);
  }
}

for (const i of INTERIOR_OPTIONS_SPEC) {
  const entry = interiors[i.code];
  if (!entry?.hex || !hexRe.test(entry.hex)) {
    throw new Error(`interior_missing_or_bad_hex:${i.code}`);
  }
}

if (softTops["硬顶 / 其他"]) {
  throw new Error("hardtop_option_must_be_removed");
}

console.log(
  `PAINT PALETTE SELFCHECK PASS; paints=${PAINT_OPTIONS.length} softTops=${TOP_OPTIONS_SPEC.length} interiors=${INTERIOR_OPTIONS_SPEC.length}`,
);
