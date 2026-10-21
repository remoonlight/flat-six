import fs from "node:fs";
import path from "node:path";

export function definitionLibraryRoot(userData, platform = process.platform) {
  // Python's Windows file APIs need the extended path form when the OS's
  // global long-path setting is off. Keep this local to our private library.
  return platform === "win32"
    ? path.win32.toNamespacedPath(path.win32.join(userData, "diagnostic-library"))
    : path.posix.join(userData, "diagnostic-library");
}

// Unpacked Windows delivery has its own Node/Python and writable definitions.
// No development checkout, global PATH or private captures are required.
export function installedRuntime({ packaged, repoRoot, userData, env = process.env }) {
  if (!packaged) {
    // Development and unpacked Mac runs must use the same persistent imported
    // library as a packaged app when the checkout has no private definitions.
    // Never search disposable acceptance directories or silently unpack ZIPs.
    const definitionsRoot = env.PORSCHE981_DEFINITION_ROOT ||
      (fs.existsSync(path.join(repoRoot, ".local/diagnostics/realtime-preparation/index.json"))
        ? repoRoot : definitionLibraryRoot(userData));
    if (definitionsRoot === repoRoot && !env.PORSCHE981_DEFINITION_ROOT) return { env, definitionsRoot };
    return { definitionsRoot, env: { ...env,
      PORSCHE981_DEFINITION_ROOT: definitionsRoot,
      PORSCHE981_VARIANTS: env.PORSCHE981_VARIANTS || path.join(definitionsRoot, ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"),
      PORSCHE981_REALTIME_PREPARATION: env.PORSCHE981_REALTIME_PREPARATION || path.join(definitionsRoot, ".local/diagnostics/realtime-preparation"),
      PORSCHE981_LANGUAGE: env.PORSCHE981_LANGUAGE || path.join(definitionsRoot, ".local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP"),
    } };
  }
  const node = path.join(repoRoot, "runtime", "node.exe");
  const python = path.join(repoRoot, "runtime", "python", "python.exe");
  for (const file of [node, python]) if (!fs.existsSync(file)) throw new Error(`installed-runtime-missing:${path.basename(file)}`);
  const definitionsRoot = definitionLibraryRoot(userData);
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
