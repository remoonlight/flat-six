import { spawn } from "node:child_process";
import { readdir, readFile, lstat } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

export const CAN_CAPTURE_CHANNEL = "diagnostics:can-capture";
const RUN = /^\d{8}-\d{6}-\d{6}$/;
const FLAGS = { executionEnabled: false, writePayload: null, liveVerified: false };
const fail = (error) => ({ ok: false, error, ...FLAGS });

export function createCanCaptureManager({ repoRoot, conn, gate, spawnFn = spawn, env = process.env, allowInjectedLive = false }) {
  const root = path.join(repoRoot, ".local", "mxplus-drive-can");
  let active = null;
  let last = null;
  let stopped = false;
  const children = new Set();

  function launch(args, onDoc, onClose) {
    const child = spawnFn("python", ["-m", "scripts.diagnostics.can_monitor", ...args], {
      cwd: repoRoot, windowsHide: true, env: { ...env, PYTHONIOENCODING: "utf-8" }, stdio: ["pipe", "pipe", "pipe"],
    });
    children.add(child);
    let buffer = "", diagnostic = "", protocolError = null;
    const invalid = (error) => { protocolError ||= error; child.kill(); };
    child.stdout.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      if (buffer.length > 512 * 1024) return invalid("capture-output-limit");
      const lines = buffer.split("\n"); buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const doc = JSON.parse(line);
          if (!doc || !["progress", "result"].includes(doc.type)) return invalid("capture-protocol-error");
          onDoc(doc);
        } catch { return invalid("capture-protocol-error"); }
      }
    });
    child.stderr.on("data", (chunk) => { diagnostic = (diagnostic + chunk.toString("utf8")).slice(-2048); });
    child.once("error", (error) => { protocolError = `capture-spawn:${error.message}`; });
    child.once("close", (code) => {
      children.delete(child);
      if (buffer.trim()) protocolError ||= "capture-truncated-output";
      onClose(protocolError, code, diagnostic);
    });
    return child;
  }

  async function list() {
    let entries;
    try { entries = await readdir(root, { withFileTypes: true }); }
    catch (error) { return error.code === "ENOENT" ? { ok: true, runs: [] } : fail("capture-list-failed"); }
    const runs = [];
    for (const item of entries.filter((e) => e.isDirectory() && !e.isSymbolicLink() && RUN.test(e.name)).sort((a, b) => b.name.localeCompare(a.name)).slice(0, 100)) {
      try {
        const state = JSON.parse(await readFile(path.join(root, item.name, "status.json"), "utf8"));
        runs.push({ id: item.name, startedUtc: state.started_utc, frames: state.frame_count || 0, qualityOk: state.ok === true, state: state.state });
      } catch { /* unfinished or absent manifest */ }
    }
    return { ok: true, runs, ...FLAGS };
  }

  async function start(request, ownerId) {
    if (stopped || active) return fail("busy");
    if (env.PORSCHE981_HEADLESS === "1" || env.PORSCHE981_SESSION_DENY_LIVE === "1" || (spawnFn !== spawn && !allowInjectedLive)) return fail("live_not_enabled");
    if (request.confirmedReadOnly !== true || request.x431Inactive !== true) return fail("live-confirmations-required");
    const seconds = request.seconds ?? 20;
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 60) return fail("capture-duration-invalid");
    const deviceId = conn.selectedId();
    if (!/^bt:[0-9A-F]{12}$/.test(deviceId || "")) return fail("mxplus-device-not-selected");
    if (!conn.reserveDispatch()) return fail("busy");
    const pending = { jobId: randomUUID(), ownerId, state: "starting", latest: null, final: null, child: null };
    active = pending; // reserve before awaiting the voltage-monitor shutdown
    let acquired = false;
    try {
      const suspended = await conn.suspendForSession();
      if (!suspended.ok) throw new Error(suspended.error || "monitor-close-failed");
      if (!gate.tryAcquire("session")) throw new Error("busy");
      acquired = true;
      const job = pending;
      if (job.cancelRequested || stopped) throw new Error("cancelled");
      job.state = "running";
      job.done = new Promise((resolve) => { job.resolve = resolve; });
      const finish = async (error, code) => {
        clearTimeout(job.deadline); clearTimeout(job.killTimer);
        if (error || !job.final || (code !== 0 && job.final.ok)) job.error = error || "capture-child-failed";
        job.state = job.final?.capture?.state === "cancelled" ? "cancelled" : job.error || !job.final?.ok ? "failed" : "completed";
        last = job; active = null;
        gate.release("session");
        try { await conn.resumeAfterSession(); }
        catch { job.error ||= "capture-resume-failed"; job.state = "failed"; }
        finally { job.resolve(); }
      };
      job.child = launch(["--stdio"], (doc) => {
        if (doc.type === "result") {
          if (job.final || typeof doc.ok !== "boolean" || !doc.capture || doc.capture.device_id !== deviceId) throw new Error("capture-protocol-error");
          job.final = doc;
        } else job.latest = doc;
      }, finish);
      job.deadline = setTimeout(() => { job.error = "capture-process-deadline"; cancelOwned(ownerId); }, (seconds + 40) * 1000);
      job.child.stdin.on("error", () => { job.error = "capture-input-failed"; job.child.kill(); });
      job.child.stdin.write(JSON.stringify({ action: "start", deviceId, seconds, confirmedReadOnly: true, x431Inactive: true }) + "\n");
      if (job.cancelRequested || stopped) cancelOwned(ownerId);
      return { ok: true, jobId: job.jobId, ...FLAGS };
    } catch (error) {
      // A child may already own the port when writing stdin fails. Keep the
      // shared gate until its close handler has finished transport cleanup.
      if (pending.child) {
        pending.error = error.message;
        pending.child.kill();
        return fail(error.message);
      }
      active = null; if (acquired) gate.release("session"); await conn.resumeAfterSession();
      return fail(error.message);
    }
  }

  function cancelOwned(ownerId) {
    if (!active || active.ownerId !== ownerId) return;
    const job = active;
    job.cancelRequested = true;
    if (!job.child || job.state === "cancelling") return;
    job.state = "cancelling";
    job.killTimer = setTimeout(() => { job.error = "capture-cancel-timeout"; job.child.kill(); }, 10_000);
    try { job.child.stdin.write('{"action":"cancel"}\n'); }
    catch { job.error = "capture-input-failed"; job.child.kill(); }
  }

  async function handle(request, { ownerId = 0 } = {}) {
    if (!request || Array.isArray(request) || typeof request !== "object") return fail("invalid-request");
    const keys = { list: ["action"], start: ["action", "seconds", "confirmedReadOnly", "x431Inactive"],
      status: ["action", "jobId"], cancel: ["action", "jobId"], replay: ["action", "runId"] }[request.action];
    if (!keys || Object.keys(request).some((k) => !keys.includes(k))) return fail("invalid-request");
    if (request.action === "list") return list();
    if (request.action === "start") return start(request, ownerId);
    if (request.action === "replay") {
      if (stopped || !RUN.test(request.runId || "")) return fail("invalid-run-id");
      try {
        const stat = await lstat(path.join(root, request.runId));
        if (!stat.isDirectory() || stat.isSymbolicLink()) return fail("invalid-run-id");
      } catch { return fail("capture-run-not-found"); }
      return new Promise((resolve) => {
        let result;
        const child = launch(["--replay-directory", path.join(root, request.runId)], (doc) => { result = doc; }, (error, code) => {
          clearTimeout(timer); resolve(error || code !== 0 || !result?.integrityVerified ? fail(error || "capture-replay-failed") : { ...result, ...FLAGS });
        });
        const timer = setTimeout(() => child.kill(), 20_000);
      });
    }
    const job = active?.jobId === request.jobId ? active : last?.jobId === request.jobId ? last : null;
    if (!job || job.ownerId !== ownerId) return fail("capture-job-not-found");
    if (request.action === "cancel") cancelOwned(ownerId);
    return { ok: true, jobId: job.jobId, state: job.state, error: job.error || null, latest: job.latest, final: job.final, ...FLAGS };
  }

  return { handle, cancelOwned, async shutdown() {
    stopped = true;
    if (active) { const job = active; cancelOwned(job.ownerId); if (job.done) await job.done; }
    for (const child of children) child.kill();
  } };
}
