/**
 * Background-only: scan data/petka/<zone>/shots/overview.{png,jpg,jpeg},
 * update .local/locator-shots-status.json, optionally run accept:locator.
 * Never touches PETKA GUI / screenshots / input / window activation.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const statusPath = path.join(root, ".local", "locator-shots-status.json");
const ZONES = ["engine-bay", "brakes", "chassis"];
const EXTS = [".png", ".jpg", ".jpeg"];

function findOverview(zoneId) {
  const shotsDir = path.join(root, "data", "petka", zoneId, "shots");
  const relDir = `data/petka/${zoneId}/shots`;
  for (const ext of EXTS) {
    const abs = path.join(shotsDir, `overview${ext}`);
    if (fs.existsSync(abs)) {
      return {
        shots_dir: relDir,
        overview_path: `${relDir}/overview${ext}`,
        overview_exists: true,
        overview_ext: ext,
      };
    }
  }
  return {
    shots_dir: relDir,
    overview_path: `${relDir}/overview.png`,
    overview_exists: false,
    overview_ext: null,
  };
}

function runAcceptLocator() {
  return new Promise((resolve) => {
    // Invoke accept script via node (same as package.json accept:locator).
    // Avoid spawning npm.cmd on Windows (Node 24 spawn EINVAL / shell deprecation).
    const acceptScript = path.join(__dirname, "locator-accept.mjs");
    const child = spawn(process.execPath, [acceptScript], {
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
      process.stdout.write(s);
    });
    child.stderr.on("data", (b) => {
      const s = b.toString();
      stderr += s;
      process.stderr.write(s);
    });
    child.on("close", (code) => {
      resolve({
        command: "npm run accept:locator",
        ran: true,
        exit_code: code ?? 1,
        pass: code === 0 && stdout.includes("LOCATOR ACCEPT PASS"),
        stdout: stdout.trimEnd(),
        stderr: stderr.trimEnd(),
      });
    });
    child.on("error", (err) => {
      resolve({
        command: "npm run accept:locator",
        ran: true,
        exit_code: 1,
        pass: false,
        stdout: stdout.trimEnd(),
        stderr: String(err),
      });
    });
  });
}

const paths = {};
let overviewPlacedCount = 0;
for (const zoneId of ZONES) {
  const info = findOverview(zoneId);
  paths[zoneId] = info;
  if (info.overview_exists) overviewPlacedCount += 1;
}

const allMissing = overviewPlacedCount === 0;
const checkedAt = new Date().toISOString();

console.log(
  `[apply-locator-shots] overview_placed_count=${overviewPlacedCount}/${ZONES.length}`,
);
for (const z of ZONES) {
  const p = paths[z];
  console.log(
    `  ${z}: ${p.overview_exists ? "FOUND " + p.overview_path : "missing"}`,
  );
}

// Placeholders still PASS accept; run always so status records a fresh result.
const accept = await runAcceptLocator();

const status = {
  task: "P-Loc-1 overview from user-dropped PETKA shots (background apply)",
  checked_at: checkedAt,
  placed_real_shots: overviewPlacedCount > 0,
  overview_placed_count: overviewPlacedCount,
  paths,
  blocker: allMissing
    ? {
        status: "waiting",
        blocked_reason: "waiting_for_user_drop",
        reason:
          "三区 overview.{png,jpg,jpeg} 均未落盘。用户手截后放入约定路径，再跑 npm run apply:locator-shots（或 watch）。占位底图下 accept:locator 仍应 PASS。",
      }
    : overviewPlacedCount < ZONES.length
      ? {
          status: "partial",
          blocked_reason: "waiting_for_user_drop",
          reason: `已有 ${overviewPlacedCount}/${ZONES.length} 区真图；其余区仍缺 overview，继续 waiting_for_user_drop。`,
        }
      : {
          status: "ready",
          blocked_reason: null,
          reason: "三区 overview 均已落盘。",
        },
  user_action: {
    summary:
      "在已打开的 PETKA Porsche 会话里手操打开三区爆炸总览，各截一张图存到约定路径，再跑 apply:locator-shots / watch。",
    drop_paths: ZONES.map(
      (z) => path.join(root, "data", "petka", z, "shots", "overview.png"),
    ),
    also_accepted: ["overview.jpg", "overview.jpeg"],
    apply: "npm run apply:locator-shots",
    watch: "npm run watch:locator-shots",
  },
  accept_locator: accept,
  note: "本脚本仅读写仓库文件并跑 accept；绝不操作 PETKA GUI / 截屏 / 键鼠 / 激活窗口。",
};

fs.mkdirSync(path.dirname(statusPath), { recursive: true });
fs.writeFileSync(statusPath, JSON.stringify(status, null, 2) + "\n", "utf8");
console.log(`[apply-locator-shots] wrote ${path.relative(root, statusPath)}`);
console.log(
  `[apply-locator-shots] accept pass=${accept.pass} exit=${accept.exit_code} blocked_reason=${status.blocker.blocked_reason ?? "none"}`,
);

process.exit(accept.pass ? 0 : 1);
