import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";
import { classifyAdapter } from "@porsche981/domain";
import { ProductionService, createMockProduction, MOCK_ADAPTERS } from "../../../packages/obd/src/production.mjs";
import { enumerateWindowsPorts } from "../../../packages/obd/src/windows-ports.mjs";
import { PowerShellSerialTransport } from "../../../packages/obd/src/powershell-serial.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

function readJson(rel) {
  const file = path.join(repoRoot, rel);
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export function loadProductionSeeds() {
  return {
    reference: readJson("data/seed/obd/981-reference.json"),
    codes: readJson("data/seed/dtc/manuals/by-code/codes.json"),
  };
}

export function createProductionHost({ dbCall, publishLive = () => {}, openBluetooth = async () => {}, mock = process.env.PORSCHE981_OBD_MOCK === "1" }) {
  if (mock) {
    const db = path.resolve(process.env.PORSCHE981_DB || '.local/garage.db');
    const rel = path.relative(os.tmpdir(), db);
    if (process.env.PORSCHE981_OBD_SMOKE !== '1' || !rel || rel.startsWith('..') || path.isAbsolute(rel)) throw new Error('obd_mock_requires_isolated_temp_database');
  }
  const { reference, codes } = loadProductionSeeds();
  const persist = (op, value) => dbCall("obdProd:op", {op, value:value ?? {}});
  const svc = mock
    ? createMockProduction({ persist, reference, codes })
    : new ProductionService({
      persist,
      reference,
      codes,
      mock: false,
      listAdapters: enumerateWindowsPorts,
      transportFactory: (adapter) => new PowerShellSerialTransport(adapter),
      openBluetooth,
    });
  const emit = () => publishLive(svc.snapshot());
  async function wrap(fn) {
    try { await initialized; const operation = fn(); emit(); const r = await operation; emit(); return r; }
    catch (e) { emit(); throw e; }
  }
  const initialized = persist("adapter:get", {}).then((pref) => {
    if (pref?.port) {
      svc.selected = classifyAdapter(pref.port, pref.friendlyName, pref.pnpId ?? null);
      emit();
    }
  }).catch(() => {});
  return {
    mock,
    snapshot: () => svc.snapshot(),
    listAdapters: () => wrap(() => mock ? Promise.resolve(MOCK_ADAPTERS) : svc.listAdapters()),
    selectAdapter: (a) => wrap(() => svc.selectAdapter(a)),
    connect: () => wrap(() => svc.connect()),
    disconnect: () => wrap(() => svc.disconnect()),
    pollStatus: () => wrap(() => svc.pollStatus()),
    scanFaults: () => wrap(() => svc.scanFaults()),
    clearDtcs: () => wrap(() => svc.clearDtcs()),
    listEcus: () => svc.listEcus(),
    listSavedVehicles: () => persist("vehicle:list", {}),
    getSavedVehicle: () => persist("vehicle:getView", {}),
    setSavedVehicle: (vehicleKey) => persist("vehicle:setView", { vehicleKey }),
    listSavedEcus: (vehicleKey) => persist("ecu:list", { vehicleKey }),
    listChanges: (vehicleKey) => persist("ecu:changes", { vehicleKey }),
    openBluetooth: () => openBluetooth(),
    readAnalysis: (selections) => wrap(() => svc.readAnalysis(selections)),
    async shutdown() { await svc.disconnectInternal().catch(() => {}); },
  };
}
