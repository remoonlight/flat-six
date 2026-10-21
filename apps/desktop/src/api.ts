import type { CodingSnapshot as CodingWorkspaceSnapshot, CodingReviewInput, CodingResultInput } from "./obd/coding-workspace-state.mjs";
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

export type CodingCaptureRequest = { clientId: string } & (
  { action: "open" | "cancel" | "discard" | "clear" }
  | { action: "analyze"; captureId: string; generation: "981" | "982"; profileId: string; runId: string }
  | { action: "page"; analysisId: string; cursor: number; stream?: "responses" | "values" | "faults" | "blocks" }
  | { action: "save"; analysisId: string });
export type CodingSourceCaptureRequest = Exclude<CodingCaptureRequest, { action: "analyze" }>
  | { action: "units"; clientId: string; generation: "981" | "982"; sourceCatalogId: string; systemId: string }
  | { action: "scope"; clientId: string; generation: "981" | "982"; profileId: string; sourceCatalogId: string; ecuId: number }
  | { action: "analyze"; clientId: string; captureId: string; generation: "981" | "982"; profileId: string; runId: string; sourceCatalogId: string; ecuId: number; captureTargetProfileId: string };
export type DtcCaptureRequest = Exclude<CodingCaptureRequest, { action: "analyze" }>
  | { action: "scope"; clientId: string; generation: "981" | "982"; profileId: string; sourceCatalogId: string; ecuId: number }
  | { action: "analyze"; clientId: string; captureId: string; generation: "981" | "982"; profileId: string; runId: string; sourceCatalogId: string; ecuId: number; linkIndex: number; captureTargetProfileId: string };
export type IdentityCaptureRequest = Exclude<CodingCaptureRequest, { action: "analyze" }>
  | { action: "scope"; clientId: string; generation: "981" | "982"; sourceCatalogId: string; ecuId: number }
  | { action: "analyze"; clientId: string; captureId: string; generation: "981" | "982"; runId: string; sourceCatalogId: string; ecuId: number; captureTargetProfileId: string };
export type CodingCaptureResult = {
  ok: boolean; error?: string; canceled?: boolean; saved?: boolean; filePath?: string; fileName?: string;
  captureId?: string; analysisId?: string; nextCursor?: number | null;
  captureTargets?: { targetProfileId: string; association: string }[];
  unit?: { ecuId: number; variants: { profileId: string; name: string }[] } | null; plan?: unknown;
  links?: { linkIndex: number; requests: string[]; missing: { reason: string }[] }[];
  sessions?: { targetProfileId: string; runId: string; synthetic: boolean; packets: number }[];
  capture?: { packets: number; prefixComplete: boolean; error: string | null; packetErrors: number; sha256: string };
  report?: { profileId?: string; runId: string; definitionFieldCount: number; requestGroupCount: number;
    blockSet?: { captureIntegrityComplete: boolean; reports: { source: "observed" | "synthetic"; counts: Record<string, number>; consistentReadSetAvailable: boolean }[] };
    versionRuleComparison?: { routeCount: number; reports: {
      routeIndexOffset: number; definitionOffset: number; source: "observed" | "synthetic";
      sourceVariantCount: number; calculationComplete: boolean;
      missingRequests: string[]; conflictingRequests: string[]; failedRequests: string[];
      status: string; error: string | null;
      matchingCandidates: { variant: string; matchingRules: { allFieldsCompared: boolean }[] }[];
    }[] };
    missingReadRequests: string[]; definitionMissing: { reason: string; sourceOffset?: number }[];
    counts: Record<string, number>; changedGroups: { requestHex: string; distinctValues: number }[];
    sourceGeneration?: string | null; sourceFieldCount?: number; decodedSourceFieldCount?: number;
    syntheticDecodedSourceFieldCount?: number; nameMissing?: unknown[]; fieldCounts?: Record<string, number>;
    recordCounts?: Record<string, number>; definitionCount?: number;
    candidateVersionCount?: number; fieldObservationCounts?: Record<string, number>;
    profiles?: { sourceProfileId: string; sourceFieldCount: number; observedFieldsWithAgreement: number;
      syntheticFieldsWithAgreement: number; missingObservedFields: { requestHex: string; fieldIndex: number }[];
      conflictingFields: { source: string; requestHex: string; fieldIndex: number }[]; sourceMissing: unknown[] }[];
    captureTargetProfileId?: string; sourceProfileId?: string;
    reviewContext?: { association: string; sourceSelectionIsVersionProof: false; identityQualified: false };
    capture: { packets: number; prefixComplete: boolean; error: string | null; packetErrors: number; sha256: string } };
  rows?: { packet: number; requestHex?: string; status?: string; pduHex?: string; dataHex?: string;
    source?: "observed" | "synthetic"; observations?: number; validResponses?: number; failures?: Record<string, number>; conflicting?: boolean;
    representative?: { packet: number; timestampUs: number; pduHex: string; dataHex: string; dataSha256: string } | null;
    minimumKnownBytes?: number; expectedTotalBytes?: number | null;
    decodedObservation?: { ok: boolean; recordCount: number } | null;
    recordIndex?: number; dtcHex?: string; statusHex?: string; sourceDisplayCode?: string | null; sourceText?: string | null; sourceTextState?: string | null; definitionBindingStatus?: string;
    parameterId?: string; name?: string; unit?: string | null; elapsedHostMs?: number;
    result?: { ok: boolean; value?: unknown; display?: string; text?: string; reason?: string };
    sourceProfileId?: string; sourceFieldIndex?: number; sourceName?: string | null; independentDecoderAgreement?: boolean; issue?: string | null;
    value?: { ok: boolean; value?: unknown; phys?: unknown; text?: string; hex?: string; reason?: string };
    timestampUs: number; synthetic?: boolean; error?: string }[];
};
export type PorscheApi = {
  workshopFlashIndex?: () => Promise<WorkshopFlashResult>;
  workshopExecution?: (request: WorkshopExecutionRequest) => Promise<WorkshopExecutionResult>;
  firmwareVersions?: (request: FirmwareVersionRequest) => Promise<FirmwareVersionResult>;
  codingWorkspace?: (request: CodingWorkspaceRequest) => Promise<CodingWorkspaceResponse>;
  firmwareExecution?: (request: FirmwareExecutionRequest) => Promise<FirmwareExecutionResult>;
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
  setCodingDraftDirty?: (dirty: boolean) => Promise<{ ok: boolean }>;
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
  manufacturerStream?: (request: { action: "start" | "start-read" | "identify" | "identify-vehicle" | "read-dtc" | "status" | "retry" | "stop" | "save" | "discard"; generation?: "981" | "982"; ecuId?: number; profileId?: string; parameterIds?: string[]; continuous?: boolean; confirmedReadOnly?: boolean; x431Inactive?: boolean; expectedVehicleIdentitySha256?: string; sessionId?: string; cursor?: number; connectionCursor?: number; episode?: number; attempt?: number }) => Promise<{
    ok: boolean; error?: string | null; sessionId?: string; state?: string; stopReason?: string | null; cursor?: number; hasMore?: boolean; events?: OfflineDiagnosticsResult[]; result?: OfflineDiagnosticsResult | null;
    saved?: boolean; canceled?: boolean; filePath?: string; saveId?: string; released?: boolean; releaseVerified?: boolean; plotElapsedMs?: number | null;
    connectionEvents?: ManufacturerConnectionEvent[]; connectionCursor?: number; hasMoreConnectionEvents?: boolean;
    unavailableParameters?: { parameterId: string; reason: string; nrc: number; processedUtc: string }[];
    invalidParameters?: { parameterId: string; reason: string; processedUtc: string }[];
    awaitingRetry?: boolean;
  }>;
  offlineDiagnostics?: (
    request: OfflineDiagnosticsRequest,
    operationId?: string,
  ) => Promise<OfflineDiagnosticsResult>;
  diagnosticPreparation?: (request: { action: string; ecu: string; id?: string; did?: string; blockKey?: string; recordAt?: number; rawValue?: number; physicalValue?: string; scenario?: string; offset?: number; edits?: CodingDraftEdit[] }) => Promise<DiagnosticPreparationResult>;
  codingCapture?: (request: CodingCaptureRequest | CodingSourceCaptureRequest) => Promise<CodingCaptureResult>;
  measurementCapture?: (request: CodingCaptureRequest) => Promise<CodingCaptureResult>;
  dtcCapture?: (request: DtcCaptureRequest) => Promise<CodingCaptureResult>;
  identityCapture?: (request: IdentityCaptureRequest) => Promise<CodingCaptureResult>;
  diagnosticDefinitionBundle?: (request: { action: "export" | "import" }) => Promise<{ ok: boolean; error?: string; canceled?: boolean; files?: number; imported?: number }>;
  diagnosticCanRecording?: (request: { action: "status" | "start" | "stop"; simulation?: boolean; layer?: "can" | "application" }) => Promise<{ ok: boolean; error?: string; canceled?: boolean;
    recording?: { active: boolean; simulation: boolean; layer?: "can" | "application"; frameCount: number; file: string; error?: string; reason?: string } | null }>;
  cancelOfflineDiagnostics?: (operationId: string) => Promise<OfflineDiagnosticsResult>;
  saveDiagnosticRecording?: (input: { fileName: string; recording: object }) =>
    Promise<{ ok: boolean; saved: boolean; canceled?: boolean; filePath?: string; error?: string }>;
  revealParameterRecording?: (request: { saveId: string }) => Promise<{ ok: boolean; error?: string }>;
  acquisitionReplay?: (request: ReplayRequest) => Promise<ReplayResult>;
  readOnlySession?: (
    request: ReadOnlySessionRequest,
  ) => Promise<ReadOnlySessionResult>;
  obdConnection?: (
    request: ObdConnectionRequest,
  ) => Promise<ObdConnectionResult>;
  canCapture?: (request: CanCaptureRequest) => Promise<CanCaptureResult>;
}

export type ReplayRequest = { action: "open" } | { action: "discard"; recordingId: string }
  | { action: "view"; recordingId: string; parameterIds: string[]; positionMs: number; offset: number; seriesOffset?: number; connectionOffset?: number };
export type ReplayRow = { index: number; parameterId: string; name: string; unit: string; value: number | string;
  display: string; numeric: boolean; timeMs: number; sourceElapsedMs: number | null; timestamp: string;
  timestampKind: string; timestampUs?: number; connectionEpoch?: number; cycle: number | null; synthetic: boolean; sourceKind: string };
export type ReplayConnectionEvent = ManufacturerConnectionEvent & { timeMs: number };
export type ReplaySummary = { fileName: string; kind: string; sourceKind: string; profileId: string; simulation: boolean;
  vehicleDataCollected: boolean; retentionComplete: boolean; timeRegressed: boolean; sampleCount: number; durationMs: number;
  startedUtc: string; endedUtc: string; stopReason: string; error: string; connectionEventCount: number; sourceStartElapsedMs: number | null;
  parameters: { id: string; name: string; unit: string; count: number }[] };
export type ReplaySeries = { id: string; name: string; unit: string; current: ReplayRow | null;
  numericCount: number; plottedCount: number; points: ReplayRow[] };
export type ReplayResult = { ok: boolean; canceled?: boolean; error?: string; recordingId?: string; summary?: ReplaySummary;
  rows?: ReplayRow[]; offset?: number; nextOffset?: number; total?: number; positionMs?: number; series?: ReplaySeries[];
  seriesOffset?: number; nextSeriesOffset?: number; seriesTotal?: number;
  connectionEvents?: ReplayConnectionEvent[]; connectionOffset?: number; nextConnectionOffset?: number; connectionTotal?: number;
  currentConnectionEvent?: ReplayConnectionEvent | null };

export type CodingBlockIdentifier = { did?: string; identifierKind?: "LID" | "DID"; identifierHex?: string };
export type CodingFieldEffect = { sourceOffset: number; selected: boolean; name: string;
  before: { raw?: number | null; value?: number | string | null; text?: string | null; phys?: string | null; display?: string | null; unit?: string | null };
  after: { raw?: number | null; value?: number | string | null; text?: string | null; phys?: string | null; display?: string | null; unit?: string | null } };
export type CodingNumericDomain = { kind: "source-bounded-linear-candidate"; unit?: string;
  physicalEnvelope: { numerator: string; denominator: string }[]; physicalStepMagnitude: { numerator: string; denominator: string };
  editorEnabled: false; nativeEditingBehaviorVerified: false };
export type CodingOperationSummary = { operationId: string; scope: "current-ecu" | "unassigned";
  ecu?: string; mode?: "simulation" | "vehicle"; phase?: string; backupId?: string; createdUtc?: string; updatedUtc?: string;
  targetBlocks?: number; changedBlocks?: number; intendedBlocks?: number; verifiedBlocks?: number; integrityError?: string;
  requiresReconciliation: boolean; automaticWriteRetry: false; executionEnabled: false };
export type FirmwareIdentifierDeclaration = { id: string | null; name: string | null; longName: string | null;
  values: { attributes: Record<string, string>; text: string; hasChildren: boolean }[] };
export type FirmwareContainerInspection = {
  schemaVersion: 1; kind: "ecu-firmware-container-inspection";
  file: { name: string; bytes: number; sha256: string }; recognized: boolean; complete: boolean;
  counts: { documents: number; sessions: number; blocks: number; flashdata: number; externalPayloads: number; inlinePayloads: number; issues: number };
  documents: { member: string; modelVersion: string | null; partNumbers: string[];
    identityMatchQualified: false;
    sessions: { id: string | null; name: string | null; memoryId: string | null;
      expectedIdentifiers: FirmwareIdentifierDeclaration[] }[];
    blocks: { id: string | null; name: string | null; memoryId: string | null;
      ownIdentifiers: FirmwareIdentifierDeclaration[] }[];
    memoryConnectors: { id: string | null; name: string | null;
      memoryReference: { attributes: Record<string, string> | null; resolved: boolean };
      sessionDescriptions: { name: string | null; direction: string | null; partNumber: string | null;
        sessionReference: { attributes: Record<string, string> | null; resolved: boolean; targetId: string | null };
        diagnosticReferences: Record<string, string>[] }[];
      identityDescriptions: { identifierReference: Record<string, string> | null;
        localCandidates: { id: string | null; kind: string; ownerId: string | null; ownerKind: string }[];
        diagnosticReferences: Record<string, string>[]; outputParameterReferences: Record<string, string>[] }[];
      layerReferences: Record<string, string>[] }[];
    flashdata: { id: string | null; name: string | null; encryptCompressMethod: { text: string } | null;
      payload: { kind: string; resolved: boolean; bytes?: number; sha256?: string; declaredName?: string } | null }[] }[];
  issues: { reason: string; member?: string; declaredName?: string }[];
  executionEnabled: false; liveVerified: false; writePayload: null;
};
export type CodingDraftEdit = { blockKey: string; recordAt: number; rawValue?: number; physicalValue?: string };
export type DiagnosticPreparationResult = { ok: boolean; error?: string; canceled?: boolean; saved?: boolean; editCount?: number;
  filePath?: string; id?: string; provenance?: string; freshReadProven?: boolean;
  operations?: CodingOperationSummary[];
  history?: { offset: number; limit: number; total: number; currentEcuTotal: number; unassignedTotal: number; requiresReconciliation: number };
  backups?: { id: string; identity: Record<string, string>; profileId: string; capturedUtc: string; original: boolean; blockCount: number; provenance?: string }[];
  backup?: { identity: Record<string, string>; profileId: string; blocks: (CodingBlockIdentifier & { dataHex: string })[] };
  options?: { rawValue: number; label: string }[]; numericDomain?: CodingNumericDomain;
  decoded?: { text?: string; raw?: number; value?: number | string; phys?: string; display?: string; unit?: string };
  targetDecoded?: { phys?: string; display?: string; unit?: string };
  beforeHex?: string; afterHex?: string; changedBitMaskHex?: string;
  effects?: CodingFieldEffect[];
  plan?: { kind?: string; changedBlocks: number; blockers: string[]; blocks: (CodingBlockIdentifier & { beforeHex: string; targetHex: string; changed: boolean })[] };
  result?: { ok: boolean; simulation: boolean; error?: string; stages: string[];
    operationId?: string | null; operationState?: string | null; journalError?: string | null; recoveryRequired?: boolean };
  preparation?: { hashMatches: boolean; targetSoftware: string; file: { name: string; sha256: string; bytes: number }; blockers: string[]; container?: FirmwareContainerInspection | null;
    transportRequirement: { targetFamilies: ("VNCI" | "PT3G")[]; selectedFamily: "VNCI" | "PT3G" | null;
      targetFamilySelected: boolean; connectedForDiagnostics: boolean; connectionType: "bluetooth" | "unverified"; wiredConnectionVerified: false } };
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
  | "source-catalogs" | "source-units" | "source-profile"
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
  physicalValue?: string;
  identity?: Record<string, unknown>;
  parameterIds?: string[];
  cycleCount?: number;
  groupId?: string;
  sourceCatalogId?: string;
};

export type ManufacturerConnectionEvent = {
  sequence: number; episode: number; phase: "interrupted" | "awaiting-retry" | "retrying" | "qualified" | "recovered" | "retry-failed" | "failed";
  attempt: number; connectionEpoch: number; elapsedMs: number; observedUtc: string; reason: string | null;
  linkReleased?: boolean;
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
  | "configure-network"
  | "configure-serial"
  | "record-start"
  | "record-stop"
  | "save-result"
  | "new-batch"
  | "receive-stop"
  | "connect"
  | "voltage"
  | "disconnect"
  | "clear"
  | "status";

export type ObdCanNetwork = "drive" | "chassis" | "comfort" | "crash" | "adas";
export type CodingWorkspaceRequest = {action:"capability"|"read"|"backup";ecu:string}
  | {action:"preview";ecu:string;readId:string;edits:({fieldId:string;optionId:string}|{fieldId:string;physicalValue:string}|{fieldId:string;textValue:string})[]}
  | {action:"preview-restore";ecu:string;readId:string}
  | {action:"write"|"restore";ecu:string;reviewId:string};
export type CodingWorkspaceResponse = {ok:boolean;error?:string;readReady?:boolean;backupReady?:boolean;saved?:boolean;filePath?:string;
  snapshot?:CodingWorkspaceSnapshot;review?:CodingReviewInput;result?:CodingResultInput};

export type FirmwareVersionRequest = { action: "list"; ecu: string } | { action: "review"; ecu: string; listId: string; targetId: string }
  | { action: "clear"; listId: string };
export type FirmwareVersionResult = { ok: boolean; error?: string; ecu?: string; listId?: string; targetId?: string;
  reviewId?: string; canStart?: boolean;
  catalogueState?: "source-unavailable" | "no-match" | "matched";
  executionEnabled: false; reason?: string; versions?: { id: string; version: string; notes?: string }[];
  rows?: { ecu: string; label: string; currentVersion: string; targetVersion: string; operation: "upgrade" | "reflash" | "downgrade" }[] };
export type FirmwareExecutionRequest = { action: "start"; ecu: string; listId: string; targetId: string; reviewId: string }
  | { action: "status" | "cancel" | "dismiss"; jobId?: string }
  | { action: "continue"; jobId: string; stepId: string };
export type FirmwareExecutionResult = { ok: boolean; error?: string; busy?: boolean; job?: WorkshopExecutionJob & {
  ecu: string; units: { ecu: string; label: string; originalVersion: string; targetVersion: string; actualVersion: string | null;
    status: "waiting" | "flashing" | "checking" | "completed" | "failed" | "unconfirmed" | "unexecuted" }[];
} | null };

export type WorkshopExecutionRequest = {
  action: "capability" | "start" | "status" | "cancel" | "continue" | "check-resume" | "resume" | "dismiss";
  functionId?: string; generation?: "981" | "982"; jobId?: string; stepId?: string;
  persistence?: "ignition-cycle" | "persistent";
};
export type WorkshopExecutionJob = {
  id: string; functionId: string; generation: "981" | "982"; title: string;
  phase: "preparing" | "running" | "waiting-manual" | "stopping" | "releasing" | "release-pending" | "checking-resume" | "resume-ready" | "completed" | "cancelled" | "failed";
  currentIndex: number; cancelRequested: boolean; cancelNotApplied: boolean; released: boolean;
  failure: { cause: string; remedy: string } | null;
  releaseFailure: { cause: string; remedy: string } | null;
  resumeFailure: { cause: string; remedy: string } | null;
  connectionWarning: string | null; resumeSupported: boolean; liveVerified: false;
  steps: { id: string; label: string; kind: "software" | "manual"; status: "pending" | "running" | "waiting-manual" | "completed" | "failed" | "unconfirmed" | "incomplete" }[];
};
export type WorkshopExecutionResult = { ok: boolean; error?: string; ready?: boolean; busy?: boolean;
  reason?: string | null; connectedForDiagnostics?: boolean; job?: WorkshopExecutionJob | null };

export type ObdConnectionRequest = {
  action: ObdConnectionAction;
  deviceId?: string;
  model?: "vLinker" | "OBDLink MX+" | "ELM327" | "OBDKey" | "OBDLink" | "CANtieCAR" | "VNCI" | "PT3G";
  purpose?: "diagnostic" | "internal";
  canNetwork?: ObdCanNetwork;
  host?: string | null;
  port?: number | null;
  serialBaud?: number | null;
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
  transport?: "d-pdu-usb" | "bluetooth-spp" | "usb-serial" | "tcp";
  serial?: string;
  address?: string | null;
};

export type ObdConnectionResult = {
  ok: boolean;
  networkEndpoint?: { host: string; port: number } | null;
  serialBaud?: number | null;
  effectiveSerialBaud?: number | null;
  error?: string | null;
  connectionError?: string | null;
  adapterInfo?: { serial: string; driverVersion: string; firmwareImage: string;
    managementSerialMatched: true; driverFirmwareMatched: true; vehicleQualified: false;
    identityPageSha256: string; versionPageSha256: string; observedAt: number } | null;
  executionEnabled: boolean;
  liveVerified: boolean;
  writePayload: null;
  selectedDeviceId?: string | null;
  model?: string | null;
  purpose?: "diagnostic" | "internal";
  canNetwork?: ObdCanNetwork | null;
  internalSupported?: boolean;
  internal?: { startedAt: string; endedAt: string | null; frameCount: number; retainedFrames: number; frameLimit: number | null;
    retainedBytes?: number; allocatedBytes?: number; retentionComplete?: boolean;
    batchClosed: boolean; stopReason?: string | null; receiveBatchId?: string; receiveState?: "idle" | "starting" | "receiving" | "stopping" | "standby"; interruptions: { at: string; reason: string }[]; verifiedSignalCount: number;
    parameterSampleCount?: number; plotElapsedMs?: number; signalDefinitionError?: boolean;
    parameters?: { id: string; name: string; nameEn?: string; unit: string; value: number | null; lastElapsedMs: number | null;
      invalid: boolean; segments: { elapsedMs: number; value: number }[][] }[];
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

export type ReadOnlySessionAction = "prepare" | "start" | "status" | "cancel" | "overview" | "saveEngine" | "discardEngine" | "retry";

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
  engineBatchId?: string;
  jobId?: string;
  episode?: number; attempt?: number;
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
  retainedEngine?: Record<string, unknown> | null;
  resumed?: boolean;
  released?: boolean;
  releaseVerified?: boolean;
  plotElapsedMs?: number | null;
  engineBatchId?: string;
  awaitingRetry?: boolean; connectionEvents?: ManufacturerConnectionEvent[];
  stopReason?: string | null;
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
