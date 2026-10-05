import fs from "node:fs";
import path from "node:path";

// Unpacked Windows delivery has its own Node/Python and writable definitions.
// No development checkout, global PATH or private captures are required.
export function installedRuntime({ packaged, repoRoot, userData, env = process.env }) {
  if (!packaged) return { env, definitionsRoot: repoRoot };
  const node = path.join(repoRoot, "runtime", "node.exe");
  const python = path.join(repoRoot, "runtime", "python", "python.exe");
  for (const file of [node, python]) if (!fs.existsSync(file)) throw new Error(`installed-runtime-missing:${path.basename(file)}`);
  const definitionsRoot = path.join(userData, "diagnostic-library");
  const seed = path.join(repoRoot, "data", "seed", "diagnostics");
  if (fs.existsSync(seed)) {
    fs.mkdirSync(path.join(definitionsRoot, "data", "seed"), { recursive: true });
    fs.cpSync(seed, path.join(definitionsRoot, "data", "seed", "diagnostics"), { recursive: true, force: false, errorOnExist: false });
  }
  return { definitionsRoot, env: { ...env,
    PORSCHE981_NODE: env.PORSCHE981_NODE || node,
    PORSCHE981_PYTHON: env.PORSCHE981_PYTHON || python,
    PORSCHE981_DEFINITION_ROOT: definitionsRoot,
    PORSCHE981_CONNECTION_STATE: env.PORSCHE981_CONNECTION_STATE || path.join(userData, "diagnostics", "connection.json"),
    PORSCHE981_SESSION_ARTIFACT_ROOT: env.PORSCHE981_SESSION_ARTIFACT_ROOT || path.join(userData, "diagnostics", "sessions"),
    PORSCHE981_LOCAL_ROOT: env.PORSCHE981_LOCAL_ROOT || path.join(userData, "local-assets"),
    PORSCHE981_CAN_CAPTURE_ROOT: env.PORSCHE981_CAN_CAPTURE_ROOT || path.join(userData, "diagnostics", "raw-captures"),
    PORSCHE981_VARIANTS: path.join(definitionsRoot, ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"),
    PORSCHE981_REALTIME_PREPARATION: path.join(definitionsRoot, ".local/diagnostics/realtime-preparation"),
    PORSCHE981_LANGUAGE: path.join(definitionsRoot, ".local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP"),
  } };
}
