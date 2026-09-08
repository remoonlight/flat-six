import { app, BrowserWindow, ipcMain, shell } from "electron";

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



const __dirname = path.dirname(fileURLToPath(import.meta.url));

const repoRoot = path.resolve(__dirname, "../../..");



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

    width: 1280,

    height: 840,

    webPreferences: {

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



app.on("window-all-closed", () => {

  bridgeCtrl.stop();

  if (process.platform !== "darwin") app.quit();

});


