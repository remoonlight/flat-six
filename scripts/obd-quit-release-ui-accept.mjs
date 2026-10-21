/** Real Electron quit handlers with a test-owned lease; no device or vehicle. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repo = path.resolve(import.meta.dirname, "..");
const entry = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(repo, "apps/desktop/electron/main.mjs"));
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(repo, ".local/obd-quit-release-20261014/source-ui"));
await fs.mkdir(output, { recursive: true });
const checks = [];
for (const action of ["quit", "window-close", "auxiliary-close"]) {
  const scratch = await fs.mkdtemp(path.join(output, action + "-"));
  let app;
  try {
    const directory = path.dirname(entry);
    let source = await fs.readFile(entry, "utf8");
    const marker = "const transportGate = createTransportGate();";
    assert.equal(source.split(marker).length, 2);
    // Instrument only a private entry copy; production exposes no test IPC.
    source = source.replace(marker, "const transportGate = globalThis.__quitAcceptGate = createTransportGate();")
      .replace("const __dirname = path.dirname(fileURLToPath(import.meta.url));", `const __dirname = ${JSON.stringify(directory)};`)
      .replace(/(from\s+)(["'])(\.[^"']+)\2/g, (_match, prefix, _quote, name) => prefix + JSON.stringify(pathToFileURL(path.resolve(directory, name)).href));
    const privateEntry = path.join(scratch, "main.mjs"); await fs.writeFile(privateEntry, source);
    const fixture = path.join(scratch, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
    const env = { ...process.env, PORSCHE981_USER_DATA: path.join(scratch, "user-data"),
      PORSCHE981_DB: path.join(scratch, "test.db"), PORSCHE981_LOCAL_ROOT: path.join(scratch, "assets"),
      PORSCHE981_CONNECTION_STATE: path.join(scratch, "connection.json"), PORSCHE981_CONNECTION_FIXTURE: fixture,
      PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1" };
    delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
    app = await electron.launch({ args: [privateEntry], env, timeout: 30000 });
    let page = await app.firstWindow(); await page.waitForLoadState("domcontentloaded");
    await app.evaluate(({ dialog }) => {
      globalThis.__quitAcceptMessages = [];
      dialog.showMessageBox = async (...args) => {
        globalThis.__quitAcceptMessages.push(args.at(-1)); return { response: 0 };
      };
      if (!globalThis.__quitAcceptGate.tryAcquire("session")) throw new Error("test lease unavailable");
    });
    if (action === "auxiliary-close") {
      const nextWindow = app.waitForEvent("window");
      await app.evaluate(({ BrowserWindow }) => {
        const original = BrowserWindow.getAllWindows()[0];
        const auxiliary = new BrowserWindow({ show: false });
        void auxiliary.loadURL("about:blank"); original.close();
      });
      page = await nextWindow;
    }
    await app.evaluate(({ app, BrowserWindow }, operation) => {
      if (operation === "quit") app.quit(); else BrowserWindow.getAllWindows()[0].close();
    }, action);
    const deadline = Date.now() + 8000;
    while (await app.evaluate(() => globalThis.__quitAcceptMessages.length === 0)) {
      assert(Date.now() < deadline, "quit release notice timed out");
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    const retained = await app.evaluate(({ BrowserWindow }) => ({ windows: BrowserWindow.getAllWindows().length,
      owner: globalThis.__quitAcceptGate.owner(), notice: globalThis.__quitAcceptMessages.at(-1) }));
    assert.equal(retained.windows, 1); assert(!page.isClosed());
    assert.equal(retained.owner, "session"); assert.equal(retained.notice.message, "正在等待诊断任务和诊断头释放");
    checks.push(action + ": renderer and lease retained while release is pending");
    await app.evaluate(() => globalThis.__quitAcceptGate.release("session"));
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
    checks.push(action + ": release does not automatically exit");
    const closed = app.waitForEvent("close", { timeout: 10000 });
    await app.evaluate(({ app }) => { setTimeout(() => app.quit(), 0); });
    await closed; app = null;
    checks.push(action + ": next manual quit exits after release");
  } finally {
    await app?.close().catch(() => {});
  }
}
await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed: true, checks,
  platform: process.platform, windowsExecuted: process.platform === "win32", deviceIO: false, vehicleIO: false,
  maintenanceClientQualified: false }, null, 2) + "\n");
console.log(`PASS quit release UI: ${checks.length} checks`);
