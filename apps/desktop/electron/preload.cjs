const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("porsche981", {
  workshopFlashIndex: () => ipcRenderer.invoke("workshop:flashIndex"),
  workshopExportPreview: (input) => ipcRenderer.invoke("workshop:exportPreview", input),
  obdGetState: () => ipcRenderer.invoke("obd:getState"),
  obdStartSimulation: (input) => ipcRenderer.invoke("obd:startSimulation", input),
  obdStop: () => ipcRenderer.invoke("obd:stop"),
  obdListRuns: () => ipcRenderer.invoke("obd:listRuns"),
  obdReplay: (id) => ipcRenderer.invoke("obd:replay", id),
  obdExport: (id) => ipcRenderer.invoke("obd:export", id),
  obdLive: () => ipcRenderer.invoke("obd:live"),
  obdListAdapters: () => ipcRenderer.invoke("obd:listAdapters"),
  obdSelectAdapter: (a) => ipcRenderer.invoke("obd:selectAdapter", a),
  obdConnect: () => ipcRenderer.invoke("obd:connect"),
  obdDisconnect: () => ipcRenderer.invoke("obd:disconnect"),
  obdPollStatus: () => ipcRenderer.invoke("obd:pollStatus"),
  obdScanFaults: () => ipcRenderer.invoke("obd:scanFaults"),
  obdClearDtcs: () => ipcRenderer.invoke("obd:clearDtcs"),
  obdListEcus: () => ipcRenderer.invoke("obd:listEcus"),
  obdListSavedVehicles: () => ipcRenderer.invoke("obd:listSavedVehicles"),
  obdGetSavedVehicle: () => ipcRenderer.invoke("obd:getSavedVehicle"),
  obdSetSavedVehicle: (vehicleKey) => ipcRenderer.invoke("obd:setSavedVehicle", vehicleKey),
  obdListSavedEcus: (vehicleKey) => ipcRenderer.invoke("obd:listSavedEcus", vehicleKey),
  obdListChanges: (vehicleKey) => ipcRenderer.invoke("obd:listChanges", vehicleKey),
  obdOpenBluetooth: () => ipcRenderer.invoke("obd:openBluetooth"),
  obdReadAnalysis: (selections) => ipcRenderer.invoke("obd:readAnalysis", selections),
  onObdState: (cb) => {
    const handler = (_e, state) => cb(state);
    ipcRenderer.on("obd:state", handler);
    return () => ipcRenderer.removeListener("obd:state", handler);
  },
  onObdLive: (cb) => {
    const handler = (_e, state) => cb(state);
    ipcRenderer.on("obd:live", handler);
    return () => ipcRenderer.removeListener("obd:live", handler);
  },
  getVehicle: () => ipcRenderer.invoke("vehicle:get"),
  setMileage: (km) => ipcRenderer.invoke("vehicle:setMileage", km),
  setAvgKmPerDay: (avg) => ipcRenderer.invoke("vehicle:setAvgKmPerDay", avg),
  setVin: (vin) => ipcRenderer.invoke("vehicle:setVin", vin),
  setVehicleSettings: (payload) => ipcRenderer.invoke("vehicle:setSettings", payload),
  listParts: () => ipcRenderer.invoke("parts:list"),
  updatePartPrices: (payload) => ipcRenderer.invoke("parts:updatePrices", payload),
  updatePartNames: (payload) => ipcRenderer.invoke("parts:updateNames", payload),
  partInterval: (partId) => ipcRenderer.invoke("parts:interval", partId),
  listService: () => ipcRenderer.invoke("service:list"),
  addService: (input) => ipcRenderer.invoke("service:add", input),
  removeService: (id) => ipcRenderer.invoke("service:remove", id),
  listFaults: () => ipcRenderer.invoke("faults:list"),
  listFaultLogs: () => ipcRenderer.invoke("faultLogs:list"),
  addFaultLog: (input) => ipcRenderer.invoke("faultLogs:add", input),
  closeFaultLog: (id) => ipcRenderer.invoke("faultLogs:close", id),
  searchDtc: (prefix) => ipcRenderer.invoke("dtc:search", prefix),
  getDtc: (code) => ipcRenderer.invoke("dtc:get", code),
  listObdSessions: () => ipcRenderer.invoke("obdSessions:list"),
  createObdSession: (input) => ipcRenderer.invoke("obdSessions:create", input),
  addObdDtc: (input) => ipcRenderer.invoke("obdDtcs:add", input),
  listObdDtcs: (sessionId) => ipcRenderer.invoke("obdDtcs:list", sessionId),
  obdDiag: (request) => ipcRenderer.invoke("obdDiag:op", request),
  codingMenu: () => ipcRenderer.invoke("coding:menu"),
  listCoding: () => ipcRenderer.invoke("coding:list"),
  addCoding: (input) => ipcRenderer.invoke("coding:add", input),
  locatorHotspots: () => ipcRenderer.invoke("locator:hotspots"),
  locatorMap: () => ipcRenderer.invoke("locator:map"),
  locatorSystemsDraft: () => ipcRenderer.invoke("locator:systemsDraft"),
  getFx: () => ipcRenderer.invoke("fx:get"),
  wiringIndex: () => ipcRenderer.invoke("wiring:index"),
  openWiringPdf: (kind, id) =>
    ipcRenderer.invoke("wiring:openPdf", { kind, id }),
  cmsListAssets: () => ipcRenderer.invoke("cms:listAssets"),
  cmsReadGlb: (rel) => ipcRenderer.invoke("cms:readGlb", rel),
  cmsGetMeshMap: () => ipcRenderer.invoke("cms:getMeshMap"),
  cmsUpsertMeshLink: (link) => ipcRenderer.invoke("cms:upsertMeshLink", link),
  cmsRemoveMeshLink: (zoneId, meshName) =>
    ipcRenderer.invoke("cms:removeMeshLink", { zoneId, meshName }),
  xrayList: () => ipcRenderer.invoke("xray:list"),
  xrayLayers: () => ipcRenderer.invoke("xray:layers"),
  xrayGarageLayers: () => ipcRenderer.invoke("xray:garageLayers"),
  xrayGetTransforms: () => ipcRenderer.invoke("xray:getTransforms"),
  xraySetTransform: (assemblyId, transform) =>
    ipcRenderer.invoke("xray:setTransform", { assemblyId, transform }),
  xrayGetMeshState: () => ipcRenderer.invoke("xray:getMeshState"),
  xraySetMeshEntry: (assemblyId, meshName, patch) =>
    ipcRenderer.invoke("xray:setMeshEntry", {
      assemblyId,
      meshName,
      ...patch,
    }),
  xraySetLayerVisible: (assemblyId, visible) =>
    ipcRenderer.invoke("xray:setLayerVisible", { assemblyId, visible }),
  xrayBridge: (assemblyId) => ipcRenderer.invoke("xray:bridge", assemblyId),
  openXrayTuneWindow: (arg) => ipcRenderer.invoke("window:openXrayTune", arg),
  modelOemLinksGet: () => ipcRenderer.invoke("modelOem:get"),
  modelOemLinksUpsert: (link) => ipcRenderer.invoke("modelOem:upsert", link),
  modelOemLinksRemove: (kind, ref, sku, assemblyId) =>
    ipcRenderer.invoke("modelOem:remove", { kind, ref, sku, assemblyId }),
  modelOemCatalog: () => ipcRenderer.invoke("modelOem:catalog"),
  offlineDiagnostics: (request, operationId) =>
    ipcRenderer.invoke("diagnostics:offline", request, operationId),
  cancelOfflineDiagnostics: (operationId) => ipcRenderer.invoke("diagnostics:offlineCancel", operationId),
  saveDiagnosticRecording: (input) => ipcRenderer.invoke("diagnostics:saveRecording", input),
  readOnlySession: (request) =>
    ipcRenderer.invoke("diagnostics:session", request),
  obdConnection: (request) =>
    ipcRenderer.invoke("diagnostics:connection", request),
  canCapture: (request) => ipcRenderer.invoke("diagnostics:can-capture", request),
  getBridgeStatus: () => ipcRenderer.invoke("db-bridge:status"),
  onBridgeStatus: (cb) => {
    const handler = (_e, status) => cb(status);
    ipcRenderer.on("db-bridge:status", handler);
    return () => ipcRenderer.removeListener("db-bridge:status", handler);
  },
});
