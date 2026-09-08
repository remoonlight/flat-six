const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("porsche981", {
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
  getBridgeStatus: () => ipcRenderer.invoke("db-bridge:status"),
  onBridgeStatus: (cb) => {
    const handler = (_e, status) => cb(status);
    ipcRenderer.on("db-bridge:status", handler);
    return () => ipcRenderer.removeListener("db-bridge:status", handler);
  },
});
