/**
 * Passive locator overview inbox: user drops screenshots into
 * .local/locator-inbox/; this script maps them to
 * data/petka/<zone>/shots/overview.<ext>, then runs apply-locator-shots.
 * Never touches PETKA GUI / screenshots / input / window activation.
 *
 * Filename conventions (see data/petka/_template/locator-inbox-README.txt):
 *   engine-bay.png|jpg|jpeg  |  engine-bay-overview.png
 *   brakes.png / chassis.png (same)
 *   or subdir: engine-bay/overview.png
 *
 * Usage:
 *   npm run ingest:locator-inbox
 *   node scripts/ingest-locator-inbox.mjs --dry-run
 *   DRY_RUN=1 node scripts/ingest-locator-inbox.mjs
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL = path.join(root, ".local");
const INBOX = path.join(LOCAL, "locator-inbox");
const DONE = path.join(INBOX, "done");
const REPORT = path.join(LOCAL, "locator-inbox-last.json");
const APPLY = path.join(__dirname, "apply-locator-shots.mjs");
const ZONES = ["engine-bay", "brakes", "chassis"];
const EXTS = new Set([".png", ".jpg", ".jpeg"]);

const dryRun =
  process.argv.includes("--dry-run") ||
  process.env.DRY_RUN === "1" ||
  process.env.LOCATOR_INBOX_DRY_RUN === "1";

function rel(p) {
  return path.relative(root, p).replace(/\\/g, "/");
}

/**
 * Resolve zone from an inbox file path (absolute).
 * Returns null if not a matching image.
 */
function matchInboxFile(absPath) {
  const ext = path.extname(absPath).toLowerCase();
  if (!EXTS.has(ext)) return null;

  const inboxRel = path.relative(INBOX, absPath);
  if (inboxRel.startsWith("..") || path.isAbsolute(inboxRel)) return null;
  const parts = inboxRel.split(/[/\\]/);
  if (parts[0] === "done") return null;

  const stem = path.basename(absPath, ext).toLowerCase();

  if (parts.length === 2) {
    const zone = parts[0].toLowerCase();
    if (!ZONES.includes(zone)) return null;
    if (stem === "overview" || stem === `${zone}-overview` || stem === zone) {
      return { zone, ext, source: absPath, pattern: "subdir" };
    }
    if (stem.includes("overview") || stem === "shot" || stem === "shots") {
      return { zone, ext, source: absPath, pattern: "subdir-alias" };
    }
    return null;
  }

  if (parts.length !== 1) return null;

  for (const zone of ZONES) {
    if (stem === zone || stem === `${zone}-overview`) {
      return { zone, ext, source: absPath, pattern: "flat" };
    }
  }
  return null;
}

function listCandidates() {
  const out = [];
  if (!fs.existsSync(INBOX)) return out;

  for (const ent of fs.readdirSync(INBOX, { withFileTypes: true })) {
    if (ent.name === "done") continue;
    if (ent.name.startsWith(".") || ent.name.startsWith("_")) continue;
    const abs = path.join(INBOX, ent.name);
    if (ent.isFile()) {
      const m = matchInboxFile(abs);
      if (m) out.push(m);
      continue;
    }
    if (ent.isDirectory() && ZONES.includes(ent.name.toLowerCase())) {
      for (const child of fs.readdirSync(abs, { withFileTypes: true })) {
        if (!child.isFile()) continue;
        if (child.name.startsWith(".") || child.name.startsWith("_")) continue;
        const m = matchInboxFile(path.join(abs, child.name));
        if (m) out.push(m);
      }
    }
  }
  return out.sort(
    (a, b) => a.zone.localeCompare(b.zone) || a.source.localeCompare(b.source),
  );
}

function pickPerZone(candidates) {
  const byZone = new Map();
  for (const c of candidates) {
    const prev = byZone.get(c.zone);
    if (!prev) {
      byZone.set(c.zone, c);
      continue;
    }
    const tNew = fs.statSync(c.source).mtimeMs;
    const tOld = fs.statSync(prev.source).mtimeMs;
    if (tNew >= tOld) byZone.set(c.zone, c);
  }
  return [...byZone.values()];
}

function destPath(zone, ext) {
  return path.join(root, "data", "petka", zone, "shots", `overview${ext}`);
}

function removeOtherOverviews(zone, keepExt) {
  const shotsDir = path.join(root, "data", "petka", zone, "shots");
  if (!fs.existsSync(shotsDir)) return [];
  const removed = [];
  for (const ext of EXTS) {
    if (ext === keepExt) continue;
    const p = path.join(shotsDir, `overview${ext}`);
    if (fs.existsSync(p)) {
      fs.unlinkSync(p);
      removed.push(rel(p));
    }
  }
  return removed;
}

function placeShot(match) {
  const dest = destPath(match.zone, match.ext);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const removed = removeOtherOverviews(match.zone, match.ext);
  fs.copyFileSync(match.source, dest);
  return { dest: rel(dest), removed_siblings: removed };
}

function moveToDone(filePath) {
  fs.mkdirSync(DONE, { recursive: true });
  const base = path.basename(filePath);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  let dest = path.join(DONE, `${stamp}__${base}`);
  let i = 0;
  while (fs.existsSync(dest)) {
    i += 1;
    dest = path.join(DONE, `${stamp}__${i}__${base}`);
  }
  fs.renameSync(filePath, dest);
  return rel(dest);
}

function runApply() {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [APPLY], {
      cwd: root,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => {
      const s = b.toString();
      stdout += s;
      process.stderr.write(s);
    });
    child.stderr.on("data", (b) => {
      const s = b.toString();
      stderr += s;
      process.stderr.write(s);
    });
    child.on("close", (code) => {
      resolve({
        command: "node scripts/apply-locator-shots.mjs",
        ran: true,
        exit_code: code ?? 1,
        pass: code === 0,
        stdout: stdout.trimEnd().slice(0, 4000),
        stderr: stderr.trimEnd().slice(0, 1000),
      });
    });
    child.on("error", (err) => {
      resolve({
        command: "node scripts/apply-locator-shots.mjs",
        ran: true,
        exit_code: 1,
        pass: false,
        stdout: stdout.trimEnd().slice(0, 4000),
        stderr: String(err),
      });
    });
  });
}

async function main() {
  fs.mkdirSync(INBOX, { recursive: true });
  fs.mkdirSync(DONE, { recursive: true });

  const all = listCandidates();
  const selected = pickPerZone(all);
  const skippedDupes = all.filter(
    (c) => !selected.some((s) => s.source === c.source),
  );

  const planned = selected.map((m) => ({
    zone: m.zone,
    pattern: m.pattern,
    source: rel(m.source),
    dest: rel(destPath(m.zone, m.ext)),
    ext: m.ext,
  }));

  const placed = [];
  const moved = [];
  let apply = null;

  if (dryRun) {
    // no copy, no move, no apply
  } else if (selected.length > 0) {
    for (const m of selected) {
      const info = placeShot(m);
      placed.push({
        zone: m.zone,
        source: rel(m.source),
        dest: info.dest,
        removed_siblings: info.removed_siblings,
      });
    }
    const toMove = [...new Set(all.map((c) => c.source))];
    for (const src of toMove) {
      try {
        if (fs.existsSync(src)) moved.push(moveToDone(src));
      } catch (e) {
        moved.push({ file: rel(src), error: String(e.message || e) });
      }
    }
    apply = await runApply();
  }

  const report = {
    ok: dryRun ? true : apply ? Boolean(apply.pass) : true,
    dry_run: dryRun,
    checked_at: new Date().toISOString(),
    inbox: rel(INBOX),
    candidates: all.map((c) => ({
      zone: c.zone,
      pattern: c.pattern,
      source: rel(c.source),
      selected: selected.some((s) => s.source === c.source),
    })),
    planned,
    skipped_dupes: skippedDupes.map((c) => rel(c.source)),
    placed,
    files_moved: moved,
    apply: dryRun
      ? { skipped: true, reason: "dry_run" }
      : selected.length === 0
        ? { skipped: true, reason: "no_matches" }
        : apply,
    note: "Background file pipeline only; never touches PETKA GUI / capture / input.",
  };

  if (!dryRun) {
    fs.mkdirSync(LOCAL, { recursive: true });
    fs.writeFileSync(REPORT, JSON.stringify(report, null, 2) + "\n", "utf8");
  }

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();