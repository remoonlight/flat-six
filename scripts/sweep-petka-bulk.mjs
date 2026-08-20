/**
 * Resumable bulk sweep checklist for 981 then 982.
 * Marks HG files present under .local/petka-bulk/{gen}/ and writes sweep-state.json.
 *
 * Usage:
 *   node scripts/sweep-petka-bulk.mjs
 *   node scripts/sweep-petka-bulk.mjs --mark 981:hg-engine
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const BULK = path.join(root, ".local", "petka-bulk");

/** Coarse PETKA-style main groups to walk (resume-friendly labels). */
const HG_LIST = [
  "engine",
  "fuel",
  "cooling",
  "exhaust",
  "transmission",
  "front-axle",
  "rear-axle",
  "brakes",
  "wheels",
  "body-front",
  "body-rear",
  "doors",
  "soft-top",
  "interior",
  "seats",
  "electrical",
  "instruments",
  "hvac",
  "lighting",
  "wiring",
];

function loadState() {
  const p = path.join(BULK, "sweep-state.json");
  if (!fs.existsSync(p)) {
    return {
      gens: {
        "981": { done: [], pending: [...HG_LIST] },
        "982": { done: [], pending: [...HG_LIST] },
      },
    };
  }
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function saveState(state) {
  fs.mkdirSync(BULK, { recursive: true });
  state.updated_at = new Date().toISOString();
  state.hg_list = HG_LIST;
  fs.writeFileSync(
    path.join(BULK, "sweep-state.json"),
    JSON.stringify(state, null, 2),
  );
}

function scanGen(gen) {
  const dir = path.join(BULK, gen);
  const files = fs.existsSync(dir)
    ? fs.readdirSync(dir).filter((f) => f.endsWith(".txt"))
    : [];
  const done = files.map((f) => f.replace(/\.txt$/i, ""));
  const pending = HG_LIST.filter(
    (h) => !done.some((d) => d === h || d === `hg-${h}` || d.startsWith(h)),
  );
  return { done, pending, files };
}

const markArg = process.argv.includes("--mark")
  ? process.argv[process.argv.indexOf("--mark") + 1]
  : null;

const state = loadState();
if (markArg) {
  const [gen, hg] = markArg.split(":");
  if ((gen !== "981" && gen !== "982") || !hg) {
    console.error("use --mark 981:engine");
    process.exit(1);
  }
  const g = state.gens[gen] || { done: [], pending: [...HG_LIST] };
  if (!g.done.includes(hg)) g.done.push(hg);
  g.pending = (g.pending?.length ? g.pending : HG_LIST).filter((x) => x !== hg);
  state.gens[gen] = g;
}

for (const gen of ["981", "982"]) {
  const scanned = scanGen(gen);
  state.gens[gen] = {
    ...state.gens[gen],
    ...scanned,
  };
}

saveState(state);
console.log(JSON.stringify({ ok: true, state }, null, 2));
