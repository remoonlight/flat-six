import type {
  AdapterIdentity,
  AnalysisBatch,
  AnalysisSelection,
  ClearReport,
  EcuChange,
  EcuIdentity,
  LiveHeader,
  ModuleCapability,
  ObdRuntimeState,
  ObdRun,
  ObdScenario,
  ScanSnapshot,
  WorkshopFlashResult,
  WorkshopPreviewInput,
} from "@porsche981/domain";
export type Vehicle = {
  id: number;
  year: number;
  model: string;
  trim: string;
  chassis: string;
  vin: string | null;
  current_km: number;
  avg_km_per_day: number | null;
  paint_name: string | null;
  paint_code: string | null;
  interior: string | null;
  top: string | null;
  updated_at: string;
};

export type VehicleSettings = {
  paint_name: string | null;
  paint_code: string | null;
  interior: string | null;
  top: string | null;
};

export type AftermarketQuote = {
  brand: string;
  price: number;
};

export type Part = {
  id: number;
  sku: string;
  name_zh: string;
  name_en?: string | null;
  oem_number: string | null;
  system: string;
  generation: string | null;
  interval_km: number | null;
  interval_months: number | null;
  oem_price: number | null;
  aftermarket_price: number | null;
  aftermarket_quotes: AftermarketQuote[] | null;
  price_note: string | null;
  price_as_of: string | null;
  locator_hotspot: string | null;
  notes: string | null;
  petka_note?: string | null;
  pr_label?: string | null;
};

export type ServiceRecord = {
  id: number;
  part_id: number | null;
  title: string;
  replaced_at: string;
  odometer_km: number;
  brand: string | null;
  cost: number | null;
  notes: string | null;
};

export type IntervalResult = {
  nextDueKm: number | null;
  nextDueDate: string | null;
  remainingKm: number | null;
  remainingDays: number | null;
  status: "ok" | "dueSoon" | "overdue" | "no_baseline";
};

export type FaultEntry = {
  id: number;
  symptom: string;
  likely_causes: string;
  checks: string;
  related_part_sku: string | null;
};

export type FaultLog = {
  id: number;
  logged_at: string;
  odometer_km: number;
  symptom: string;
  area_hypothesis: string | null;
  action: string | null;
  result: string | null;
  closed: number;
  related_part_sku: string | null;
  coding_snapshot_id: number | null;
};

export type DtcEntry = {
  code: string;
  title_zh: string;
  likely_causes: string;
  checks: string;
  related_part_sku: string | null;
};

export type ObdSession = {
  id: number;
  started_at: string;
  odometer_km: number | null;
  adapter: string | null;
  note: string | null;
};

export type ObdDtc = {
  id: number;
  session_id: number;
  code: string;
  status: string;
  raw_json: string | null;
};

export type CodingSnapshot = {
  id: number;
  system: string;
  function_name: string;
  sub_function: string | null;
  before_value: string;
  after_value: string;
  note: string | null;
  odometer_km: number;
  recorded_at: string;
};

export type CodingMenu = {
  year?: string;
  model?: string;
  rowCount?: number;
  systemCount?: number;
  systems: Array<{
    system: string;
    items: Array<{
      function: string;
      subFunction: string | null;
      playbook: {
        risk: string;
        steps: string[];
        x431Path: string;
      };
    }>;
  }>;
};

export type Hotspot = { id: string; label: string; x: number; y: number };

export type CmsZoneHint = {
  present: boolean;
  assetId: string | null;
  rel: string | null;
  caveatZh?: string;
};

export type CmsAsset = {
  id: string;
  label_zh: string;
  rel: string;
  present: boolean;
  bytes: number;
  attribution?: string;
  /** Locator 对照浏览；不得作产品幽灵壳 / zone 默认 */
  browseOnly?: boolean;
};

export type CmsMeshLink = {
  zoneId: string;
  meshName: string;
  hotspotId: string;
  assetId?: string | null;
  sku?: string | null;
  note?: string | null;
};

export type CmsMeshMap = {
  version: number;
  note: string;
  caveatZh?: string;
  updated_at: string | null;
  links: CmsMeshLink[];
};

export type ModelOemKind = "assembly" | "mesh" | "flow";

export type ModelOemLink = {
  id: string;
  kind: ModelOemKind;
  ref: string;
  assemblyId?: string | null;
  skus: string[];
  note?: string | null;
};

export type ModelOemLinkFile = {
  version: number;
  note: string;
  updated_at: string | null;
  links: ModelOemLink[];
};

export type ModelOemCatalogItem = {
  kind: ModelOemKind;
  ref: string;
  label: string;
  assemblyId: string | null;
};

export type ModelOemCatalog = {
  /** 父块：车身 / 装配 / flow */
  parents: ModelOemCatalogItem[];
  /** parent.ref → 子 mesh（flow 则为自身） */
  childrenByParent: Record<string, ModelOemCatalogItem[]>;
  assemblies: ModelOemCatalogItem[];
  bodyMeshes: ModelOemCatalogItem[];
  flows: ModelOemCatalogItem[];
  path: string;
};

export type XrayTransform = {
  position: number[];
  rotationEuler: number[];
  scale: number[];
};

export type XrayMeshEntry = {
  visible: boolean;
  transform: XrayTransform;
};

export type XrayLayerMeshState = {
  visible: boolean;
  meshes: Record<string, XrayMeshEntry>;
};

export type XrayMeshState = {
  version: number;
  note: string;
  layers: Record<string, XrayLayerMeshState>;
};

export type XrayAssembly = {
  id: string;
  label: string;
  label_zh: string;
  glbRel: string;
  manifestRel: string | null;
  hotspot3d: string;
  displayRadius: number | null;
  carSpace: boolean;
  worldScale: number;
  bilateral: boolean;
  lateralOffset: number;
  hideInUnified: string[];
  source: string;
  bridgeZone: string | null;
  bridgeHotspot: string | null;
  aliasOfGlb: string | null;
  /** 子 mesh TRS 沿用另一装配 id（镜像副本等） */
  meshStateOf?: string | null;
  /** 同 glbRel 再挂一份（镜像副本等） */
  duplicateGlb?: boolean;
  /** 加载时沿 X 轴镜像网格 */
  mirrorX?: boolean;
  /** X-ray 树分组：power=引擎+燃油 · drive=传动+前后轮 */
  xrayGroup?: string | null;
  xrayGroupZh?: string | null;
  /** 车库透视分栏（仅 garage-assemblies）；未设则归机械 */
  garageStructure?: string | null;
  /** 顶栏内饰子类（仅 garage-assemblies + 807 等 PETKA 内饰件） */
  interiorZone?: string | null;
  loadLayer: boolean;
  present: boolean;
  bytes: number;
  transform: XrayTransform | null;
};

export type XrayBodyShell = {
  rel: string;
  opacity: number;
  attribution: string | null;
  present: boolean;
  bytes: number;
  transform: XrayTransform | null;
};

export type XrayScene = {
  version: number;
  note: string;
  caveatZh: string;
  axle: Record<string, unknown> | null;
  bodyShell: XrayBodyShell | null;
  assemblies: XrayAssembly[];
  layers?: XrayAssembly[];
  transformsPath: string;
  flows?: GarageFlowSystem[];
};

export type GarageFlowSystem = {
  id: string;
  layer: "air" | "lines" | "vacuum" | "wiring";
  label: string;
  labelEn?: string;
  color: string;
  pipe: { color: string; metalness: number; roughness: number };
  radius: number;
  paths: { points: number[][]; closed?: boolean }[];
  nodes?: {
    id: string;
    label: string;
    at: number[];
    size: number[];
    color: string;
  }[];
};

export type LocatorZone = {
  id: string;
  label_zh: string;
  status: "pending-3d" | "ready-3d" | string;
  shotHint: string;
  /** Zone-level draft note (e.g. electronics/fluids P-Loc-2 bridge). */
  note?: string | null;
  background: {
    source: "petka-shot" | "placeholder" | string;
    dataUrl: string;
  };
  cms?: CmsZoneHint;
  hotspots: Hotspot[];
};

export type LocatorMap = {
  note: string;
  zones: LocatorZone[];
};

/** P-Loc-2 systems-draft.plan.json — fluids → P-Loc-1 part anchors. */
export type LocatorBridgeJump = {
  fromHotspotId: string;
  toZoneId: string;
  toHotspotId: string;
  sku?: string | null;
};

export type LocatorSystemsDraft = {
  version: number;
  status?: string;
  note_zh?: string;
  zones?: unknown[];
  bridgeJumps?: LocatorBridgeJump[];
};

export type WiringIndex = {
  authority: string;
  note: string;
  pdf: { id: string; label: string; path: string };
  doNotUse: Array<{ id: string; label: string; path: string }>;
  systems: Array<{
    id: string;
    label_zh: string;
    status: string;
    locatorZoneId?: string;
    locatorHotspotId?: string | null;
  }>;
};

export type BridgeStatus = {
  state: "idle" | "starting" | "ready" | "restarting" | "down" | "stopped";
  detail: string | null;
  failures: number;
  at: number;
};

/** Manual FX seed (`data/seed/fx.json`); amounts in DB stay source currency. */
export type FxTable = {
  display_currency: "CNY";
  as_of: string;
  note?: string;
  rates_to_cny: Record<string, number>;
};

export type ObdLiveSnapshot = {
  mock: boolean;
  op: string | null;
  selected: AdapterIdentity | null;
  header: LiveHeader;
  lastScan: ScanSnapshot | null;
  capabilities: ModuleCapability[];
  adapterConnected: boolean;
  vehicleCommunicating: boolean;
};

export type PorscheApi = {
  workshopFlashIndex?: () => Promise<WorkshopFlashResult>;
  workshopExportPreview?: (input: WorkshopPreviewInput) => Promise<{ saved: boolean }>;
  obdGetState: () => Promise<ObdRuntimeState>;
  obdStartSimulation: (input: { scenario: ObdScenario; budgetMs: number }) => Promise<ObdRuntimeState>;
  obdStop: () => Promise<ObdRuntimeState>;
  obdListRuns: () => Promise<ObdRun[]>;
  obdReplay: (id: number) => Promise<ObdRuntimeState>;
  obdExport: (id: number) => Promise<{ saved: boolean }>;
  obdLive: () => Promise<ObdLiveSnapshot>;
  obdListAdapters: () => Promise<AdapterIdentity[]>;
  obdSelectAdapter: (a: AdapterIdentity) => Promise<AdapterIdentity>;
  obdConnect: () => Promise<ObdLiveSnapshot>;
  obdDisconnect: () => Promise<ObdLiveSnapshot>;
  obdPollStatus: () => Promise<LiveHeader>;
  obdScanFaults: () => Promise<ScanSnapshot>;
  obdClearDtcs: () => Promise<ClearReport>;
  obdListEcus: () => Promise<EcuIdentity[]>;
  obdListSavedVehicles: () => Promise<string[]>;
  obdGetSavedVehicle: () => Promise<string | null>;
  obdSetSavedVehicle: (vehicleKey: string | null) => Promise<string | null>;
  obdListSavedEcus: (vehicleKey: string) => Promise<EcuIdentity[]>;
  obdListChanges: (vehicleKey: string) => Promise<EcuChange[]>;
  obdOpenBluetooth: () => Promise<unknown>;
  obdReadAnalysis: (selections: AnalysisSelection[]) => Promise<AnalysisBatch>;
  onObdState: (cb: (state: ObdRuntimeState) => void) => () => void;
  onObdLive: (cb: (state: ObdLiveSnapshot) => void) => () => void;
  getVehicle: () => Promise<Vehicle>;
  setMileage: (km: number) => Promise<Vehicle>;
  setAvgKmPerDay: (avg: number | null) => Promise<Vehicle>;
  setVin: (vin: string | null) => Promise<Vehicle>;
  setVehicleSettings: (settings: VehicleSettings) => Promise<Vehicle>;
  listParts: () => Promise<Part[]>;
  updatePartPrices: (payload: {
    id: number;
    oem_price: number | null;
    aftermarket_price: number | null;
    aftermarket_quotes?: AftermarketQuote[] | null;
    price_note: string | null;
    price_as_of: string | null;
  }) => Promise<Part>;
  updatePartNames: (payload: {
    sku: string;
    oem_number?: string | null;
    name_zh?: string | null;
    name_en?: string | null;
    petka_note?: string | null;
    pr_label?: string | null;
  }) => Promise<Part | null | undefined>;
  partInterval: (partId: number) => Promise<IntervalResult | null>;
  listService: () => Promise<ServiceRecord[]>;
  addService: (input: {
    part_id?: number | null;
    title: string;
    replaced_at: string;
    odometer_km: number;
    brand?: string | null;
    cost?: number | null;
    notes?: string | null;
  }) => Promise<ServiceRecord>;
  removeService: (id: number) => Promise<{ ok: true }>;
  listFaults: () => Promise<FaultEntry[]>;
  listFaultLogs: () => Promise<FaultLog[]>;
  addFaultLog: (input: {
    logged_at: string;
    odometer_km: number;
    symptom: string;
    area_hypothesis?: string | null;
    action?: string | null;
    result?: string | null;
    closed?: boolean | number;
    related_part_sku?: string | null;
    coding_snapshot_id?: number | null;
  }) => Promise<FaultLog>;
  closeFaultLog: (id: number) => Promise<FaultLog>;
  searchDtc: (prefix: string) => Promise<DtcEntry[]>;
  getDtc: (code: string) => Promise<DtcEntry | undefined>;
  listObdSessions: () => Promise<ObdSession[]>;
  createObdSession: (input: {
    started_at?: string;
    odometer_km?: number | null;
    adapter?: string | null;
    note?: string | null;
  }) => Promise<ObdSession>;
  addObdDtc: (input: {
    session_id: number;
    code: string;
    status?: string;
    raw_json?: string | null;
  }) => Promise<ObdDtc>;
  listObdDtcs: (sessionId: number) => Promise<ObdDtc[]>;
  obdDiag?: (request: { op: string; value?: Record<string, unknown> | null }) => Promise<unknown>;
  codingMenu: () => Promise<CodingMenu>;
  listCoding: () => Promise<CodingSnapshot[]>;
  addCoding: (input: {
    system: string;
    function_name: string;
    sub_function?: string | null;
    before_value: string;
    after_value: string;
    note?: string | null;
    odometer_km: number;
    recorded_at: string;
  }) => Promise<CodingSnapshot>;
  locatorHotspots: () => Promise<Hotspot[]>;
  locatorMap: () => Promise<LocatorMap>;
  locatorSystemsDraft?: () => Promise<LocatorSystemsDraft>;
  getFx?: () => Promise<FxTable | null>;
  wiringIndex: () => Promise<WiringIndex>;
  openWiringPdf: (
    kind: "main" | "ref",
    id?: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  cmsListAssets: () => Promise<CmsAsset[]>;
  cmsReadGlb: (rel: string) => Promise<Uint8Array | ArrayBuffer | { type: string; data: number[] }>;
  cmsGetMeshMap: () => Promise<CmsMeshMap>;
  cmsUpsertMeshLink: (link: CmsMeshLink) => Promise<CmsMeshMap>;
  cmsRemoveMeshLink: (zoneId: string, meshName: string) => Promise<CmsMeshMap>;
  xrayList: () => Promise<XrayScene>;
  xrayLayers: () => Promise<XrayScene>;
  xrayGarageLayers: () => Promise<XrayScene>;
  xrayGetTransforms: () => Promise<{
    version: number;
    note: string;
    layers: Record<string, XrayTransform>;
  }>;
  xraySetTransform: (
    assemblyId: string,
    transform: XrayTransform,
  ) => Promise<{
    version: number;
    note: string;
    layers: Record<string, XrayTransform>;
  }>;
  xrayGetMeshState: () => Promise<XrayMeshState>;
  xraySetMeshEntry: (
    assemblyId: string,
    meshName: string,
    patch: { visible?: boolean; transform?: XrayTransform },
  ) => Promise<XrayMeshState>;
  xraySetLayerVisible: (
    assemblyId: string,
    visible: boolean,
  ) => Promise<XrayMeshState>;
  xrayBridge: (
    assemblyId: string,
  ) => Promise<{
    assemblyId: string;
    zoneId: string | null;
    hotspotId: string | null;
  } | null>;
  openXrayTuneWindow?: (
    arg?: string | { scene?: string; asset?: string; assetId?: string },
  ) => Promise<{ ok: boolean }>;
  modelOemLinksGet?: () => Promise<ModelOemLinkFile>;
  modelOemLinksUpsert?: (link: {
    kind: ModelOemKind;
    ref: string;
    assemblyId?: string | null;
    skus: string[];
    note?: string | null;
  }) => Promise<ModelOemLinkFile>;
  modelOemLinksRemove?: (
    kind: ModelOemKind,
    ref: string,
    sku?: string,
    assemblyId?: string | null,
  ) => Promise<ModelOemLinkFile>;
  modelOemCatalog?: () => Promise<ModelOemCatalog>;
  getBridgeStatus?: () => Promise<BridgeStatus>;
  onBridgeStatus?: (cb: (status: BridgeStatus) => void) => () => void;
  offlineDiagnostics?: (
    request: OfflineDiagnosticsRequest,
    operationId?: string,
  ) => Promise<OfflineDiagnosticsResult>;
  diagnosticPreparation?: (request: { action: string; ecu: string; id?: string; did?: string; blockKey?: string; recordAt?: number; rawValue?: number; scenario?: string }) => Promise<DiagnosticPreparationResult>;
  diagnosticDefinitionBundle?: (request: { action: "export" | "import" }) => Promise<{ ok: boolean; error?: string; canceled?: boolean; files?: number; imported?: number }>;
  diagnosticCanRecording?: (request: { action: "status" | "start" | "stop"; simulation?: boolean }) => Promise<{ ok: boolean; error?: string; canceled?: boolean;
    recording?: { active: boolean; simulation: boolean; frameCount: number; file: string; error?: string; reason?: string } | null }>;
  cancelOfflineDiagnostics?: (operationId: string) => Promise<OfflineDiagnosticsResult>;
  saveDiagnosticRecording?: (input: { fileName: string; recording: object }) =>
    Promise<{ ok: boolean; saved: boolean; canceled?: boolean; filePath?: string; error?: string }>;
  readOnlySession?: (
    request: ReadOnlySessionRequest,
  ) => Promise<ReadOnlySessionResult>;
  obdConnection?: (
    request: ObdConnectionRequest,
  ) => Promise<ObdConnectionResult>;
  canCapture?: (request: CanCaptureRequest) => Promise<CanCaptureResult>;
}

export type CodingBlockIdentifier = { did?: string; identifierKind?: "LID" | "DID"; identifierHex?: string };
export type DiagnosticPreparationResult = { ok: boolean; error?: string; canceled?: boolean; saved?: boolean;
  backups?: { id: string; identity: Record<string, string>; profileId: string; capturedUtc: string; original: boolean; blockCount: number; provenance?: string }[];
  backup?: { identity: Record<string, string>; profileId: string; blocks: (CodingBlockIdentifier & { dataHex: string })[] };
  options?: { rawValue: number; label: string }[]; decoded?: { text?: string; raw?: number };
  beforeHex?: string; afterHex?: string; changedBitMaskHex?: string;
  plan?: { kind?: string; changedBlocks: number; blockers: string[]; blocks: (CodingBlockIdentifier & { beforeHex: string; targetHex: string; changed: boolean })[] };
  result?: { ok: boolean; simulation: boolean; error?: string; stages: string[] };
  preparation?: { hashMatches: boolean; targetSoftware: string; file: { name: string; sha256: string; bytes: number }; blockers: string[] };
};

export type CanCaptureRequest = { action: "list" | "start" | "status" | "cancel" | "replay";
  seconds?: number; confirmedReadOnly?: boolean; x431Inactive?: boolean; jobId?: string; runId?: string };
export type CanCaptureResult = { ok: boolean; error?: string | null; jobId?: string; state?: string;
  runs?: { id: string; startedUtc: string; frames: number; qualityOk: boolean; state: string }[];
  latest?: { state?: string; frame_count?: number }; final?: CanCaptureResult; directory?: string;
  integrityVerified?: boolean; captureQualityOk?: boolean;
  capture?: { frame_count: number; state: string; ok: boolean; adapter_notices: Record<string, number>;
    trailing_partial_bytes: number; partitions: { id_hex: string; extended: boolean; dlc: number; count: number }[];
    error?: string | null; cleanup_error?: string | null; timestamp_source: string } };

export type OfflineDiagnosticsAction =
  | "summary"
  | "plan"
  | "variants"
  | "records"
  | "match"
  | "decode"
  | "preview"
  | "coding-options"
  | "catalog-units" | "catalog-parameters" | "catalog-plan" | "catalog-replay" | "catalog-coding-plan"
  | "replay"
  | "ready-units" | "ready-parameters" | "ready-plan" | "ready-replay" | "ready-acquire";

export type OfflineDiagnosticsRequest = {
  action: OfflineDiagnosticsAction;
  generation?: "981" | "982";
  ecuId?: number;
  profileId?: string;
  category?: "identity" | "measurement" | "coding" | "dtc" | "routine";
  offset?: number;
  limit?: number;
  search?: string;
  recordAt?: number;
  dataHex?: string;
  expectedReadRequestHex?: string;
  responseMode?: "data" | "pdu";
  rawValue?: number;
  identity?: Record<string, unknown>;
  parameterIds?: string[];
  groupId?: string;
};

export type OfflineDiagnosticsResult = {
  ok: boolean;
  error?: string | null;
  executionEnabled: boolean;
  liveVerified: boolean;
  writePayload: null;
  [key: string]: unknown;
};

export type ObdConnectionAction =
  | "list"
  | "select"
  | "configure"
  | "record-start"
  | "record-stop"
  | "save-result"
  | "new-batch"
  | "connect"
  | "voltage"
  | "disconnect"
  | "clear"
  | "status";

export type ObdCanNetwork = "drive" | "chassis" | "comfort" | "crash" | "adas";

export type ObdConnectionRequest = {
  action: ObdConnectionAction;
  deviceId?: string;
  model?: "vLinker" | "OBDLink MX+" | "VNCI";
  purpose?: "diagnostic" | "internal";
  canNetwork?: ObdCanNetwork;
};

export type ObdConnectionDevice = {
  id: string;
  brand?: string;
  name?: string | null;
  comPort?: string | null;
  available?: boolean;
  paired?: boolean;
  osStatus?: string | null;
  guidance?: string | null;
  transport?: "d-pdu-usb" | "bluetooth-spp";
  serial?: string;
  address?: string | null;
};

export type ObdConnectionResult = {
  ok: boolean;
  error?: string | null;
  connectionError?: string | null;
  executionEnabled: boolean;
  liveVerified: boolean;
  writePayload: null;
  selectedDeviceId?: string | null;
  model?: string | null;
  purpose?: "diagnostic" | "internal";
  canNetwork?: ObdCanNetwork | null;
  internalSupported?: boolean;
  internal?: { startedAt: string; endedAt: string | null; frameCount: number; retainedFrames: number; frameLimit: number;
    batchClosed: boolean; interruptions: { at: string; reason: string }[]; verifiedSignalCount: number;
    latest: { canId: number; extended: boolean; dataHex: string; timestampUs: number; timestampSource: string }[] } | null;
  recording?: { active: boolean; file: string; startedAt: string; frameCount: number; error?: string; reason?: string } | null;
  connected?: boolean;
  linkState?: string;
  pairingOk?: boolean;
  commOk?: boolean;
  devices?: ObdConnectionDevice[];
  deviceRegistry?: ObdRegisteredDevice[];
  listErrors?: string[];
  voltageVolts?: number | null;
  voltageSource?: string | null;
  voltageAt?: number | null;
  voltageLabel?: string;
  [key: string]: unknown;
};

export type ObdRegisteredDevice = {
  id: string;
  family: string;
  name: string;
  serial?: string;
  mac?: string;
  firmware?: string;
  usbInstanceId?: string;
  connection: string;
  state: string;
  present: boolean;
  connectable: boolean;
  comPort: string | null;
  driverInstalled: boolean | null;
  driverVersion: string | null;
  serviceState: string | null;
  features: string[];
  routes: ("session" | "live" | "broadcast" | "offline" | "coding")[];
  note: string;
  lastSeenAt: string | null;
  checkedAt: string;
};

export type ReadOnlySessionAction = "prepare" | "start" | "status" | "cancel" | "overview";

export type ReadOnlySessionRequest = {
  action: ReadOnlySessionAction;
  profileId?: "porsche-981-2014-dme" | "porsche-981-2014-gateway";
  mode?: "simulation" | "live";
  sessionTask?: "read" | "clear" | "engine";
  scenario?:
    | "success"
    | "identity-mismatch"
    | "negative"
    | "pending-timeout"
    | "disconnect"
    | "slow";
  operationIds?: string[];
  resumeRunId?: string;
  confirmedReadOnly?: boolean;
  confirmedClearDtc?: boolean;
  x431Inactive?: boolean;
  sampleCycles?: number;
  intervalMs?: number;
  selectedPids?: string[];
  jobId?: string;
};

export type ReadOnlySessionResult = {
  ok: boolean;
  error?: string | null;
  executionEnabled: boolean;
  liveVerified: boolean;
  writePayload: null;
  jobId?: string;
  state?: string;
  taskState?: "idle" | "running" | "offline";
  plan?: unknown;
  events?: unknown[];
  latest?: Record<string, unknown> | null;
  final?: Record<string, unknown> | null;
  resumed?: boolean;
  [key: string]: unknown;
};

export function hasDesktopApi(): boolean {
  return typeof window !== "undefined" && !!window.porsche981;
}

export function offlineDiagnosticsFixtureEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env.MODE !== "offline-test") return false;
  const q = new URLSearchParams(window.location.search);
  return q.get("offline-diag-fixture") === "1" && !!window.__OFFLINE_DIAG_MOCK__;
}

export function readOnlySessionFixtureEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env.MODE !== "session-test") return false;
  return typeof window.porsche981?.readOnlySession === "function";
}

export function topologyFixtureEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env.MODE !== "topology-test") return false;
  return typeof window.porsche981?.readOnlySession === "function";
}

export function engineSessionFixtureEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env.MODE !== "engine-test") return false;
  return typeof window.porsche981?.readOnlySession === "function";
}

declare global {
  interface Window {
    porsche981: PorscheApi;
    __OFFLINE_DIAG_MOCK__?: PorscheApi["offlineDiagnostics"];
    __ROS_SEED_LIVE_RUN__?: { runId: string; key: string };
    __TOPOLOGY_EXPORT__?: unknown;
    __FAKE_SESSION_CALLS__?: unknown[];
    __TOPO_DELAY_START_MS__?: number;
    __TOPO_SCENARIO__?: string;
    __TOPO_CLEAR_RESIDUAL__?: boolean;
    __TOPO_REQUIRE_CONFIRM__?: boolean;
    __FAKE_OVERVIEW__?: { ok?: boolean; taskState?: string; voltageVolts?: number | null; voltageLabel?: string };
    __FAKE_OVERVIEW_THROW__?: boolean;
    __FAKE_CONNECTION__?: (req: ObdConnectionRequest) => Promise<ObdConnectionResult> | ObdConnectionResult;
    __OVERVIEW_DELAY_MS__?: number;
    __ENGINE_SCENARIO__?: string;
  }
}

export function api(): PorscheApi {
  if (!window.porsche981) {
    throw new Error("porsche981 API unavailable — run inside Electron");
  }
  return window.porsche981;
}

export async function callOfflineDiagnostics(
  request: OfflineDiagnosticsRequest,
  operationId?: string,
): Promise<OfflineDiagnosticsResult> {
  if (offlineDiagnosticsFixtureEnabled() && window.__OFFLINE_DIAG_MOCK__) {
    return window.__OFFLINE_DIAG_MOCK__(request);
  }
  const fn = window.porsche981?.offlineDiagnostics;
  if (!fn) {
    return {
      ok: false,
      error: "desktop_required",
      executionEnabled: false,
      liveVerified: false,
      writePayload: null,
    };
  }
  return fn(request, operationId);
}
