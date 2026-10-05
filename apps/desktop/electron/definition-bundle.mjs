import { spawn } from "node:child_process";
import { resolvePythonCandidates } from "./offline-diagnostics.mjs";

export function runDefinitionBundle(request, { repoRoot, env = process.env, spawnFn = spawn }) {
  return new Promise((resolve) => {
    const candidates = resolvePythonCandidates(env);
    function launch(index) {
      const runtime = candidates[index];
      const child = spawnFn(runtime.exe, [...runtime.prefix, "-m", "scripts.diagnostics.definition_bundle"], {
        cwd: repoRoot, windowsHide: true, env: { ...env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" }, stdio: ["pipe", "pipe", "pipe"] });
      let output = "", done = false, failure = null;
      const timer = setTimeout(() => { failure = "bundle-timeout"; child.kill(); }, 180000);
      child.stdout.on("data", (data) => { output += data.toString("utf8"); if (output.length > 32768) { failure = "bundle-output-limit"; child.kill(); } });
      child.stderr.on("data", () => {});
      child.stdin.on("error", () => {});
      child.on("error", (error) => {
        if (done) return; done = true; clearTimeout(timer);
        if (error.code === "ENOENT" && index + 1 < candidates.length) launch(index + 1);
        else resolve({ ok: false, error: error.code || "bundle-spawn-failed" });
      });
      child.on("close", (code) => {
        if (done) return; done = true; clearTimeout(timer);
        if (failure) return resolve({ ok: false, error: failure });
        try { const result = JSON.parse(output); resolve(code === 0 && result.ok ? result : { ok: false, error: result.error || "bundle-failed" }); }
        catch { resolve({ ok: false, error: "bundle-protocol-error" }); }
      });
      child.stdin.end(JSON.stringify(request));
    }
    launch(0);
  });
}
