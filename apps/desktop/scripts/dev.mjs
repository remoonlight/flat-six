import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import electronPath from "electron";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "..");
const port = Number(process.env.PORSCHE981_DEV_PORT || 5173);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid development port");
const serverUrl = `http://127.0.0.1:${port}`;

// Listen to our own server before starting Electron; an occupied port must fail
// rather than attaching a second garage window to another session's server.
let vite;
let electron;
let stopping = false;

async function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  electron?.kill();
  try {
    await vite?.close();
  } finally {
    process.exit(code);
  }
}

process.on("SIGINT", () => shutdown());
process.on("SIGTERM", () => shutdown());

try {
  vite = await createServer({ root: appRoot,
    configFile: path.join(appRoot, "vite.config.ts"),
    server: { port, strictPort: true, host: "127.0.0.1" },
  });
  if (stopping) { await vite.close(); process.exit(0); }
  await vite.listen();
  vite.printUrls();
} catch (error) {
  console.error(error);
  await shutdown(1);
}

electron = spawn(electronPath, ["."], {
  cwd: appRoot,
  env: { ...process.env, VITE_DEV_SERVER_URL: serverUrl },
  stdio: "inherit",
});

electron.on("error", (error) => { console.error(error); shutdown(1); });
electron.on("exit", (code) => shutdown(code ?? 1));
