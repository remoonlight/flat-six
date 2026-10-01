import fs from "node:fs";
import path from "node:path";
import { randomInt } from "node:crypto";
import { spawnSync } from "node:child_process";

// Use the production store without changing its routing or requiring private captures.
export function createReplayFixture(repoRoot) {
  const store = path.resolve(repoRoot, ".local", "mxplus-drive-can");
  const runId = `20990101-000000-${String(randomInt(1_000_000)).padStart(6, "0")}`;
  const directory = path.resolve(store, runId);
  if (path.dirname(directory) !== store || fs.existsSync(directory)) throw new Error("unsafe-fixture-path");
  const cleanup = () => {
    if (path.dirname(directory) !== store || !/^20990101-000000-\d{6}$/.test(path.basename(directory)))
      throw new Error("unsafe-fixture-cleanup");
    fs.rmSync(directory, { recursive: true, force: true });
  };
  const child = spawnSync("python", ["-m", "scripts.diagnostics.tests.can_replay_fixture", directory], {
    cwd: repoRoot, windowsHide: true, encoding: "utf8", timeout: 20_000,
    env: { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_SESSION_DENY_LIVE: "1" },
  });
  if (child.status !== 0) {
    cleanup();
    throw new Error(`replay-fixture-failed: ${child.error?.message || child.stderr}`);
  }
  return { runId, cleanup };
}
