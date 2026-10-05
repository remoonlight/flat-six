import { app, BrowserWindow, ipcMain, shell, dialog } from "electron";
import { createObdController } from "./obd-controller.mjs";

import { spawn } from "node:child_process";

import fs from "node:fs";

import path from "node:path";

import { fileURLToPath } from "node:url";

import { createBridgeController } from "./bridge-lifecycle.mjs";
import {
  listCmsAssets,
  loadMeshMap,
  readCmsGlb,
  removeMeshLink,
  upsertMeshLink,
} from "./cms-assets.mjs";
import {
  bridgeForAssembly,
  listGarageXrayLayers,
  listXrayAssemblies,
  listXrayLayers,
  loadXrayMeshState,
  loadXrayTransforms,
  setXrayLayerVisible,
  setXrayMeshEntry,
  setXrayTransform,
} from "./xray-assets.mjs";
import {
  listGarageModelCatalog,
  loadModelOemLinks,
  removeModelOemLink,
  upsertModelOemLink,
} from "./model-oem-links.mjs";
import { createOfflineOperations, runWorkbench } from "./offline-diagnostics.mjs";
import { createDiagnosticPreparation } from "./diagnostic-preparation.mjs";
import { runDefinitionBundle } from "./definition-bundle.mjs";
import { createDiagnosticCanRecorder } from "./diagnostic-can-recording.mjs";
import { saveRecordingFile } from "./recording-files.mjs";
import { installedRuntime } from "./installed-runtime.mjs";
import {
  SESSION_CHANNEL,
  createReadOnlySessionManager,
} from "./read-only-session.mjs";
import { CONNECTION_CHANNEL, attachSessionHandoff, createObdConnectionManager, createTransportGate } from "./obd-connection.mjs";
import { CAN_CAPTURE_CHANNEL, createCanCaptureManager } from "./can-capture.mjs";
import { readWorkshopFlashIndex, prepareWorkshopExport } from "./piwis-workshop.mjs";



const __dirname = path.dirname(fileURLToPath(import.meta.url));

const repoRoot = path.resolve(__dirname, "../../..");
if (process.env.PORSCHE981_USER_DATA) app.setPath("userData", process.env.PORSCHE981_USER_DATA);
const runtime = installedRuntime({ packaged: app.isPackaged, repoRoot, userData: app.getPath("userData") });
Object.assign(process.env, runtime.env);
const offlineOperations = createOfflineOperations({ repoRoot,
  variantsPath: process.env.PORSCHE981_VARIANTS || undefined });



function dbPath() {

  if (process.env.PORSCHE981_DB) return process.env.PORSCHE981_DB;

  if (app.isPackaged) {

    return path.join(app.getPath("userData"), "garage.db");

  }

  return path.join(repoRoot, ".local", "garage.db");

}



function broadcastBridgeStatus(status) {

  for (const win of BrowserWindow.getAllWindows()) {

    win.webContents.send("db-bridge:status", status);

  }

}



const transportGate = createTransportGate();
const connMgr = createObdConnectionManager({ repoRoot, gate: transportGate,
  chooseRecordingFile: async () => {
    const result = await dialog.showSaveDialog({ title: "记录原始 CAN 报文", defaultPath: "can-recording.pcapng",
      filters: [{ name: "Wireshark 捕获", extensions: ["pcapng"] }] });
    return result.canceled ? null : result.filePath;
  },
  saveResult: async (recording) => saveRecordingFile({ fileName: "internal-can-result.json", recording }, {
    dialog, writeFile: fs.promises.writeFile, window: BrowserWindow.getFocusedWindow(),
  }),
});
const canCaptureMgr = createCanCaptureManager({ repoRoot, conn: connMgr, gate: transportGate });
const diagnosticCanRecorder = createDiagnosticCanRecorder({
  isLiveReady: () => { const status = connMgr.snapshot(); return status.purpose === "diagnostic"
    && ["vLinker", "OBDLink MX+"].includes(status.model) && (status.connected || status.linkState === "diagnostic"); },
  chooseFile: async (simulation) => {
    const chosen = await dialog.showSaveDialog({ title: "开始记录诊断头实际报告的接收 CAN 帧", defaultPath: `${simulation ? "simulated-" : ""}diagnostic-rx-${Date.now()}.pcapng`, filters: [{ name: "PCAPNG", extensions: ["pcapng"] }] });
    return chosen.canceled ? null : chosen.filePath;
  },
});
const sessionMgr = attachSessionHandoff(
  connMgr,
  createReadOnlySessionManager({
    repoRoot,
    gate: transportGate,
    getLiveDeviceId: () => connMgr.selectedId(),
    onCanFrame: (event) => diagnosticCanRecorder.onFrame(event),
    onSessionEnded: () => {
      void connMgr.resumeAfterSession();
    },
  }),
);

const bridgeCtrl = createBridgeController({

  maxRetries: 5,

  baseDelayMs: 400,

  maxDelayMs: 8000,

  onStatus: broadcastBridgeStatus,

  spawnBridge: () =>
    spawn(process.env.PORSCHE981_NODE || "node", [
      path.join(__dirname, "db-bridge.mjs"),
    ], {
      env: { ...process.env, PORSCHE981_DB: dbPath() },
      stdio: ["pipe", "pipe", "ignore"],
      cwd: repoRoot,
      // ponytail: Windows otherwise pops a blank node.exe console for the bridge
      windowsHide: true,
    }),
});



function call(method, params) {

  return bridgeCtrl.call(method, params);

}

const obdCtrl = createObdController({
  dbCall: call,
  publish: (state) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) win.webContents.send("obd:state", state);
    }
  },
  publishLive: (state) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) win.webContents.send("obd:live", state);
    }
  },
  openBluetooth: () => shell.openExternal("ms-settings:bluetooth"),
});



async function runVisualSmoke(win, outPng) {
  const info = await win.webContents.executeJavaScript(`
    (async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const locBtn = document.querySelector('nav button[data-tab="locator"]');
      if (!locBtn) throw new Error("nav_locator_missing");
      locBtn.click();
      await sleep(200);
      if (!locBtn.classList.contains("active")) {
        throw new Error("nav_locator_not_active");
      }
      for (let i = 0; i < 50; i++) {
        if (document.querySelector(".locator-dual")) break;
        await sleep(200);
      }
      if (!document.querySelector(".locator-dual")) {
        throw new Error("locator_page_missing");
      }
      // Ensure X-ray mode (first select in pane head is view mode)
      const modeSel = document.querySelector(".locator-pane-head select");
      if (modeSel) {
        const xrayOpt = [...modeSel.options].find((o) => o.value === "xray");
        if (xrayOpt && !xrayOpt.disabled) {
          modeSel.value = "xray";
          modeSel.dispatchEvent(new Event("change", { bubbles: true }));
          await sleep(400);
        }
      }
      for (let i = 0; i < 50; i++) {
        if (document.querySelector(".locator-glb canvas")) break;
        if (document.querySelector(".locator-glb.missing")) {
          throw new Error("locator_glb_missing_banner");
        }
        await sleep(200);
      }
      const canvas = document.querySelector(".locator-glb canvas");
      if (!canvas) throw new Error("locator_canvas_missing");
      const w = canvas.width || 0;
      const h = canvas.height || 0;
      if (w < 64 || h < 64) throw new Error("locator_canvas_too_small:" + w + "x" + h);
      const gizmo = document.querySelector(".locator-gizmo-bar select");
      if (!gizmo) throw new Error("locator_gizmo_select_missing");
      const hasEngine = [...gizmo.options].some((o) => o.value === "engine");
      if (!hasEngine) throw new Error("locator_engine_option_missing");
      gizmo.value = "engine";
      gizmo.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(400);
      const sliders = document.querySelector(".locator-sliders");
      if (!sliders) throw new Error("locator_sliders_missing");
      sliders.scrollIntoView({ block: "center" });
      await sleep(200);
      const ranges = sliders.querySelectorAll('input[type="range"]');
      if (ranges.length < 6) throw new Error("locator_sliders_count:" + ranges.length);
      const r0 = ranges[0].getBoundingClientRect();
      if (r0.width < 80 || r0.height < 8) {
        throw new Error("locator_slider_not_visible:" + r0.width + "x" + r0.height);
      }
      // Nudge X slider to prove gizmo reacts without throw
      const xRange = ranges[0];
      xRange.value = String(Number(xRange.value) + Number(xRange.step || 0.01));
      xRange.dispatchEvent(new Event("input", { bubbles: true }));
      xRange.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(500);
      return {
        w,
        h,
        sliders: ranges.length,
        sliderBox: { w: r0.width, h: r0.height },
        tab: locBtn.className,
        hasDual: !!document.querySelector(".locator-dual"),
        dock: !!document.querySelector(".locator-gizmo-dock"),
      };
    })()
  `);
  console.log("VISUAL_SMOKE_DOM", JSON.stringify(info));
  fs.mkdirSync(path.dirname(outPng), { recursive: true });
  const img = await win.webContents.capturePage();
  fs.writeFileSync(outPng, img.toPNG());
  if (img.getSize().width < 400) throw new Error("capture_too_small");
  console.log("VISUAL_SMOKE_OK", outPng);
  app.exit(0);
}

function loadAppUrl(win, hash = "") {
  const suffix = hash ? (hash.startsWith("#") ? hash : `#${hash}`) : "";
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    win.loadURL(`${devUrl.replace(/\/$/, "")}/${suffix}`);
  } else {
    win.loadFile(path.join(__dirname, "../dist/index.html"), {
      hash: suffix.replace(/^#/, ""),
    });
  }
}

let xrayTuneWin = null;

function openXrayTuneWindow(arg) {
  let hash = "xray-tune";
  let title = "X-ray 姿态微调";
  if (arg && typeof arg === "object") {
    const scene = String(arg.scene || "").trim();
    const asset = String(arg.asset || arg.assetId || "").trim();
    if (scene === "garage") {
      hash = "xray-tune?scene=garage";
      title = "车库透视 · 姿态微调";
    } else if (asset) {
      hash = `xray-tune?asset=${encodeURIComponent(asset)}`;
      title = `模型姿态微调 · ${asset}`;
    }
  } else {
    const id = arg ? String(arg).trim() : "";
    if (id) {
      hash = `xray-tune?asset=${encodeURIComponent(id)}`;
      title = `模型姿态微调 · ${id}`;
    }
  }

  if (xrayTuneWin && !xrayTuneWin.isDestroyed()) {
    xrayTuneWin.setTitle(title);
    loadAppUrl(xrayTuneWin, hash);
    xrayTuneWin.focus();
    return { ok: true };
  }

  const win = new BrowserWindow({
    width: 1100,
    height: 860,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title,
  });

  xrayTuneWin = win;
  win.on("closed", () => {
    if (xrayTuneWin === win) xrayTuneWin = null;
  });

  win.webContents.on("did-finish-load", () => {
    win.webContents.send("db-bridge:status", bridgeCtrl.getStatus());
  });

  loadAppUrl(win, hash);
  return { ok: true };
}

function createWindow() {

  const smokeOut = process.env.PORSCHE981_VISUAL_SMOKE || "";
  const win = new BrowserWindow({
    show: process.env.PORSCHE981_OBD_SMOKE !== "1",

    width: 1280,

    height: 840,

    show: process.env.PORSCHE981_HEADLESS !== "1",

    webPreferences: {

      ...(process.env.PORSCHE981_OBD_SMOKE === "1" ? { offscreen: true, backgroundThrottling: false } : {}),

      preload: path.join(__dirname, "preload.cjs"),

      contextIsolation: true,

      nodeIntegration: false,

    },

    title: "Porsche 981 Garage — 2014 Boxster S",

  });



  win.webContents.on("did-finish-load", () => {

    win.webContents.send("db-bridge:status", bridgeCtrl.getStatus());
    if (smokeOut) {
      win.show();
      // Wait for React + bridge + GLB
      setTimeout(() => {
        runVisualSmoke(win, smokeOut).catch((e) => {
          console.error("VISUAL_SMOKE_FAIL", e);
          app.exit(1);
        });
      }, 3500);
    }

  });



  loadAppUrl(win);
  // ponytail: DevTools opt-in — auto-detach spam on every `npm run dev`
  if (
    process.env.VITE_DEV_SERVER_URL &&
    !smokeOut &&
    process.env.PORSCHE981_DEVTOOLS === "1"
  ) {
    win.webContents.openDevTools({ mode: "detach" });
  }

}



function loadWiringIndex() {

  const p = path.join(repoRoot, "data", "seed", "wiring", "index.json");

  return JSON.parse(fs.readFileSync(p, "utf8"));

}



function resolveWiringPdfPath(p) {

  if (!p || typeof p !== "string") return null;

  return path.isAbsolute(p) ? p : path.join(repoRoot, p);

}



function registerIpc() {

  const methods = [

    "vehicle:get",

    "vehicle:setMileage",

    "vehicle:setAvgKmPerDay",

    "vehicle:setVin",

    "vehicle:setSettings",

    "parts:list",

    "parts:updatePrices",

    "parts:updateNames",

    "parts:interval",

    "service:list",

    "service:add",

    "service:remove",

    "faults:list",

    "faultLogs:list",

    "faultLogs:add",

    "faultLogs:close",

    "dtc:search",

    "dtc:get",

    "obdSessions:list",

    "obdSessions:create",

    "obdDtcs:add",

    "obdDtcs:list",

    "obdDiag:op",

    "coding:menu",

    "coding:list",

    "coding:add",

    "locator:hotspots",

    "locator:map",

    "locator:systemsDraft",

    "fx:get",

  ];

  for (const m of methods) {

    ipcMain.handle(m, (_e, ...args) => {

      const params = args.length <= 1 ? args[0] : args;

      return call(m, params);

    });

  }



  ipcMain.handle("db-bridge:status", () => bridgeCtrl.getStatus());

  const obdHandlers = {
    "obd:getState": () => obdCtrl.getState(),
    "obd:startSimulation": (input) => obdCtrl.start(input),
    "obd:stop": () => obdCtrl.stop(),
    "obd:listRuns": () => obdCtrl.list(),
    "obd:replay": (id) => obdCtrl.replay(id),
    "obd:export": async (id) => {
      const recording = await obdCtrl.recording(id);
      const chosen = await dialog.showSaveDialog({ title: "导出模拟采集记录", defaultPath: `obd-simulation-${recording.run.sessionId}.json`, filters: [{ name: "OBD 记录", extensions: ["json"] }] });
      if (chosen.canceled || !chosen.filePath) return { saved: false };
      await fs.promises.writeFile(chosen.filePath, JSON.stringify(recording, null, 2), "utf8");
      return { saved: true };
    },
    "obd:live": () => obdCtrl.production.snapshot(),
    "obd:listAdapters": () => obdCtrl.production.listAdapters(),
    "obd:selectAdapter": (a) => obdCtrl.production.selectAdapter(a),
    "obd:connect": () => obdCtrl.production.connect(),
    "obd:disconnect": () => obdCtrl.production.disconnect(),
    "obd:pollStatus": () => obdCtrl.production.pollStatus(),
    "obd:scanFaults": () => obdCtrl.production.scanFaults(),
    "obd:clearDtcs": () => obdCtrl.production.clearDtcs(),
    "obd:listEcus": () => obdCtrl.production.listEcus(),
    "obd:listSavedVehicles": () => obdCtrl.production.listSavedVehicles(),
    "obd:getSavedVehicle": () => obdCtrl.production.getSavedVehicle(),
    "obd:setSavedVehicle": (vehicleKey) => obdCtrl.production.setSavedVehicle(vehicleKey),
    "obd:listSavedEcus": (vehicleKey) => obdCtrl.production.listSavedEcus(vehicleKey),
    "obd:listChanges": (vehicleKey) => obdCtrl.production.listChanges(vehicleKey),
    "obd:openBluetooth": () => obdCtrl.production.openBluetooth(),
    "obd:readAnalysis": (selections) => obdCtrl.production.readAnalysis(selections),
  };
  for (const [channel, handler] of Object.entries(obdHandlers)) {
    ipcMain.handle(channel, (event, input) => {
      if (!BrowserWindow.fromWebContents(event.sender) || event.senderFrame !== event.sender.mainFrame) throw new Error("obd_invalid_sender");
      return handler(input);
    });
  }



  ipcMain.handle("wiring:index", () => loadWiringIndex());



  ipcMain.handle("wiring:openPdf", async (_e, payload) => {

    const idx = loadWiringIndex();

    const kind = payload?.kind;

    let target = null;

    if (kind === "main") target = idx.pdf;

    else if (kind === "ref") {

      target = (idx.doNotUse || []).find((f) => f.id === payload?.id) ?? null;

    }

    if (!target?.path) return { ok: false, error: "unknown_pdf" };

    const pdfPath = resolveWiringPdfPath(target.path);

    if (!pdfPath || !fs.existsSync(pdfPath)) {

      return { ok: false, error: `file_missing:${target.path}` };

    }

    const err = await shell.openPath(pdfPath);

    if (err) return { ok: false, error: err };

    return { ok: true };

  });

  ipcMain.handle("cms:listAssets", () => listCmsAssets());

  ipcMain.handle("cms:readGlb", (_e, rel) => readCmsGlb(rel));

  ipcMain.handle("cms:getMeshMap", () => loadMeshMap());

  ipcMain.handle("cms:upsertMeshLink", (_e, link) => upsertMeshLink(link));

  ipcMain.handle("cms:removeMeshLink", (_e, payload) =>
    removeMeshLink(payload?.zoneId, payload?.meshName),
  );

  ipcMain.handle("xray:list", () => listXrayAssemblies());
  ipcMain.handle("xray:layers", () => listXrayLayers());
  ipcMain.handle("xray:garageLayers", () => listGarageXrayLayers());
  ipcMain.handle("xray:getTransforms", () => loadXrayTransforms());
  ipcMain.handle("xray:setTransform", (_e, payload) =>
    setXrayTransform(payload?.assemblyId, payload?.transform),
  );
  ipcMain.handle("xray:getMeshState", () => loadXrayMeshState());
  ipcMain.handle("xray:setMeshEntry", (_e, payload) =>
    setXrayMeshEntry(payload?.assemblyId, payload?.meshName, {
      visible: payload?.visible,
      transform: payload?.transform,
    }),
  );
  ipcMain.handle("xray:setLayerVisible", (_e, payload) =>
    setXrayLayerVisible(payload?.assemblyId, payload?.visible),
  );
  ipcMain.handle("xray:bridge", (_e, assemblyId) =>
    bridgeForAssembly(assemblyId),
  );
  ipcMain.handle("window:openXrayTune", (_e, assetId) =>
    openXrayTuneWindow(assetId),
  );
  ipcMain.handle("modelOem:get", () => loadModelOemLinks());
  ipcMain.handle("modelOem:upsert", (_e, link) => upsertModelOemLink(link));
  ipcMain.handle("modelOem:remove", (_e, payload) =>
    removeModelOemLink(
      payload?.kind,
      payload?.ref,
      payload?.sku,
      payload?.assemblyId,
    ),
  );
  ipcMain.handle("diagnostics:offline", (e, request, operationId) =>
    offlineOperations.run(request, e.sender.id, operationId));
  ipcMain.handle("diagnostics:offlineCancel", (e, operationId) =>
    offlineOperations.cancel(e.sender.id, operationId));
  ipcMain.handle("diagnostics:saveRecording", (e, input) => saveRecordingFile(input, {
    dialog, writeFile: fs.promises.writeFile, window: BrowserWindow.fromWebContents(e.sender),
  }));
  ipcMain.handle("diagnostics:canRecording", (_e, request) => diagnosticCanRecorder.handle(request));

  const preparation = createDiagnosticPreparation({
    directory: path.join(app.isPackaged ? app.getPath("userData") : path.join(repoRoot, ".local"), "diagnostics", "coding-backups"),
    connectionStatus: () => connMgr.snapshot(),
    previewField: (request) => runWorkbench(request, { repoRoot, variantsPath: process.env.PORSCHE981_VARIANTS || undefined }),
    chooseOpenFile: async (kind) => {
      const options = { title: kind === "backup" ? "导入并保存当前控制单元的完整码值备份" : kind === "manifest" ? "选择原厂固件匹配清单" : "选择原厂控制单元固件文件",
        properties: ["openFile"], ...(kind === "firmware" ? {} : { filters: [{ name: "JSON", extensions: ["json"] }] }) };
      const selected = await dialog.showOpenDialog(options);
      return selected.canceled ? null : selected.filePaths[0];
    },
    saveFile: (fileName, recording) => saveRecordingFile({ fileName, recording }, { dialog, writeFile: fs.promises.writeFile }),
  });
  ipcMain.handle("diagnostics:preparation", (_e, request) => preparation.handle(request));
  let bundleBusy = false;
  ipcMain.handle("diagnostics:definitionBundle", async (_e, request) => {
    if (!request || Object.keys(request).length !== 1 || !["export", "import"].includes(request.action)) return { ok: false, error: "bundle-request-invalid" };
    if (bundleBusy || !sessionMgr.isIdle() || connMgr.snapshot().linkState !== "idle" || transportGate.owner()) return { ok: false, error: "bundle-busy-disconnect-first" };
    bundleBusy = true;
    try {
      const result = request.action === "export" ? await dialog.showSaveDialog({ title: "导出诊断资料（含离线派生样本及来源信息）", defaultPath: "diagnostic-definitions.zip", filters: [{ name: "ZIP", extensions: ["zip"] }] })
        : await dialog.showOpenDialog({ title: "导入诊断资料：已有不同内容不会覆盖", properties: ["openFile"], filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (result.canceled) return { ok: true, canceled: true };
      const file = request.action === "export" ? result.filePath : result.filePaths[0];
      if (!file) return { ok: true, canceled: true };
      if (!sessionMgr.isIdle() || connMgr.snapshot().linkState !== "idle" || transportGate.owner()) return { ok: false, error: "bundle-busy-disconnect-first" };
      return await runDefinitionBundle({ action: request.action, file }, { repoRoot });
    } finally { bundleBusy = false; }
  });

  const workshopIndexPath = path.join(runtime.definitionsRoot, ".local", "diagnostics", "piwis-workshop", "flash-index.json");
  ipcMain.handle("workshop:flashIndex", () => readWorkshopFlashIndex(workshopIndexPath));
  ipcMain.handle("workshop:exportPreview", async (_e, input) => {
    const preview = await prepareWorkshopExport(input, workshopIndexPath);
    const result = await dialog.showSaveDialog({
      title: "保存维护与编程准备清单",
      defaultPath: `workshop-${preview.generation}-${preview.functionId}.json`,
      filters: [{ name: "JSON 准备清单", extensions: ["json"] }],
    });
    if (result.canceled || !result.filePath) return { saved: false };
    await fs.promises.writeFile(result.filePath, JSON.stringify(preview, null, 2) + "\n", "utf8");
    return { saved: true };
  });

  ipcMain.handle(SESSION_CHANNEL, async (e, request) => {
    if (bundleBusy && ["start", "prepare"].includes(request?.action)) return { ok: false, error: "definition-bundle-busy" };
    const out = await sessionMgr.handle(request, { ownerId: e.sender.id });
    if (request?.action === "overview" && out?.ok) {
      const v = connMgr.voltageView();
      return { ...out, ...v, voltageLabel: connMgr.snapshot().voltageLabel };
    }
    return out;
  });

  ipcMain.handle(CONNECTION_CHANNEL, async (_e, request) => {
    if (bundleBusy && !["status", "list", "disconnect"].includes(request?.action)) return { ok: false, error: "definition-bundle-busy" };
    const out = await connMgr.handle(request);
    if (out.ok && ["select", "configure", "disconnect", "clear"].includes(request?.action)) diagnosticCanRecorder.stop("connection-settings-changed");
    return out;
  });
  ipcMain.handle(CAN_CAPTURE_CHANNEL, (e, request) => canCaptureMgr.handle(request, { ownerId: e.sender.id }));

  ipcMain.handle("modelOem:catalog", async () => {
    const scene = await listGarageXrayLayers();
    return listGarageModelCatalog({
      bodyRel: scene?.bodyShell?.rel ?? null,
      layers: (scene?.layers ?? []).filter((l) => l.present),
      flows: scene?.flows ?? [],
    });
  });

}



app.whenReady().then(() => {

  bridgeCtrl.start();

  registerIpc();

  createWindow();

  app.on("activate", () => {

    if (BrowserWindow.getAllWindows().length === 0) createWindow();

  });

});

app.on("web-contents-created", (_e, contents) => {
  contents.once("destroyed", () => {
    offlineOperations.cancelOwned(contents.id);
    sessionMgr.cancelOwned(contents.id);
    canCaptureMgr.cancelOwned(contents.id);
  });
});

let sessionQuitting = false;
app.on("before-quit", (e) => {
  diagnosticCanRecorder.shutdown();
  if (sessionQuitting) return;
  e.preventDefault();
  sessionQuitting = true;
  Promise.allSettled([sessionMgr.shutdown(), canCaptureMgr.shutdown(), connMgr.shutdown()]).finally(() => app.quit());
});


app.on("window-all-closed", async () => {

  await obdCtrl.shutdown();

  bridgeCtrl.stop();

  if (process.platform !== "darwin") app.quit();

});

let obdQuitReady = false;
app.on("before-quit", (event) => {
  if (obdQuitReady) return;
  event.preventDefault();
  obdQuitReady = true;
  obdCtrl.shutdown().finally(() => { bridgeCtrl.stop(); app.quit(); });
});
