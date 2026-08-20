import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import electronPath from "electron";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "..");

// Bind IPv4 explicitly — default localhost can be ::1 while probe uses 127.0.0.1.
const vite = spawn(
  "npx",
  ["vite", "--port", "5173", "--host", "127.0.0.1"],
  {
    cwd: appRoot,
    shell: true,
    stdio: "inherit",
  },
);

function waitForServer(url, tries = 60) {
  return new Promise(async (resolve, reject) => {
    for (let i = 0; i < tries; i++) {
      try {
        const res = await fetch(url);
        if (res.ok || res.status === 404) return resolve();
      } catch {
        /* retry */
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    reject(new Error("vite not ready"));
  });
}

await waitForServer("http://127.0.0.1:5173/");

const electron = spawn(electronPath, ["."], {
  cwd: appRoot,
  env: { ...process.env, VITE_DEV_SERVER_URL: "http://127.0.0.1:5173" },
  stdio: "inherit",
});

function shutdown() {
  electron.kill();
  vite.kill();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

electron.on("exit", () => {
  vite.kill();
  process.exit(0);
});
