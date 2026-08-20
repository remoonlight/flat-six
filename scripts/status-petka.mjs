/**
 * One-shot PETKA / locator background status (no GUI, no focus steal).
 *
 * Prints JSON + short human summary; writes .local/agent-status.json
 *
 * Usage: npm run status:petka
 *        node scripts/status-petka.mjs
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL = path.join(root, ".local");
const OUT = path.join(LOCAL, "agent-status.json");

const ZONES = ["engine-bay", "brakes", "chassis"];
const OVERVIEW_EXTS = [".png", ".jpg", ".jpeg"];
const PRICES_TSV = path.join(LOCAL, "petka-prices.tsv");
const PETKA_INBOX = path.join(LOCAL, "petka-inbox");
const LOCATOR_INBOX = path.join(LOCAL, "locator-inbox");
/** SKUs that must keep oem_price empty (VIN / no single OEM). */
const FORCE_EMPTY_PRICE_SKUS = new Set(["fuel-filter", "tire-fl"]);
const SKU_LIST_CAP = 40;
const DOMAIN_DIST_INDEX = path.join(root, "packages", "domain", "dist", "index.js");
const DOMAIN_DIST_PRICE_VERIFY = path.join(
  root,
  "packages",
  "domain",
  "dist",
  "price-verify.js",
);
const DOMAIN_DIST_PETKA_CSV = path.join(root, "packages", "domain", "dist", "petka-csv.js");

const WATCH_MARKERS = [
  "watch-petka-inbox",
  "watch-locator-inbox",
  "watch-locator-shots",
  "watch-petka-handcopy",
];

function rel(p) {
  return path.relative(root, p).replace(/\\/g, "/");
}

function isNonEmptyPrice(v) {
  if (v == null) return false;
  const s = String(v).trim();
  return s !== "" && s.toLowerCase() !== "null";
}

function truncateSkuList(skus, cap = SKU_LIST_CAP) {
  if (skus.length <= cap) {
    return { list: skus, truncated: false };
  }
  return { list: skus.slice(0, cap), truncated: true };
}

/**
 * Prefer built @porsche981/domain dist (same heuristics as app/ingest).
 * Do not copy price-verify regexes into this script.
 */
async function loadDomainCsvHelpers() {
  const importJs = async (abs) => import(pathToFileURL(abs).href);

  if (fs.existsSync(DOMAIN_DIST_INDEX)) {
    const mod = await importJs(DOMAIN_DIST_INDEX);
    if (
      typeof mod.isPetkaPriceVerified === "function" &&
      typeof mod.needsPetkaPriceVerify === "function" &&
      typeof mod.parsePetkaPartsCsv === "function"
    ) {
      return {
        ok: true,
        source: rel(DOMAIN_DIST_INDEX),
        isPetkaPriceVerified: mod.isPetkaPriceVerified,
        needsPetkaPriceVerify: mod.needsPetkaPriceVerify,
        parsePetkaPartsCsv: mod.parsePetkaPartsCsv,
      };
    }
  }

  if (
    fs.existsSync(DOMAIN_DIST_PRICE_VERIFY) &&
    fs.existsSync(DOMAIN_DIST_PETKA_CSV)
  ) {
    const [pv, csv] = await Promise.all([
      importJs(DOMAIN_DIST_PRICE_VERIFY),
      importJs(DOMAIN_DIST_PETKA_CSV),
    ]);
    if (
      typeof pv.isPetkaPriceVerified === "function" &&
      typeof pv.needsPetkaPriceVerify === "function" &&
      typeof csv.parsePetkaPartsCsv === "function"
    ) {
      return {
        ok: true,
        source: `${rel(DOMAIN_DIST_PRICE_VERIFY)}+${rel(DOMAIN_DIST_PETKA_CSV)}`,
        isPetkaPriceVerified: pv.isPetkaPriceVerified,
        needsPetkaPriceVerify: pv.needsPetkaPriceVerify,
        parsePetkaPartsCsv: csv.parsePetkaPartsCsv,
      };
    }
  }

  const hint =
    "packages/domain/dist missing or incomplete — run: npm run build -w @porsche981/domain";
  console.warn(`warn: ${hint}`);
  return { ok: false, error: hint };
}

/** Read-only scan of data/petka/{zone}/parts.csv using domain heuristics. */
function scanCsvPriceVerify(helpers) {
  const base = {
    zones: {},
    total_parts: 0,
    with_oem_price: 0,
    petka_verified: 0,
    needs_verify: 0,
    verified_skus: [],
    needs_verify_skus: [],
    forced_empty_ok: [],
    forced_empty_violations: [],
  };

  if (!helpers || !helpers.ok) {
    return {
      ...base,
      error: helpers?.error || "domain_helpers_unavailable",
    };
  }

  const {
    isPetkaPriceVerified,
    needsPetkaPriceVerify,
    parsePetkaPartsCsv,
  } = helpers;
  const verified = [];
  const needs = [];
  const forcedOk = [];
  const forcedBad = [];
  let total = 0;
  let withPrice = 0;
  const zoneStats = {};
  const parseErrors = [];

  for (const zone of ZONES) {
    const abs = path.join(root, "data", "petka", zone, "parts.csv");
    const zInfo = {
      path: rel(abs),
      present: false,
      total_parts: 0,
      with_oem_price: 0,
      petka_verified: 0,
      needs_verify: 0,
    };
    if (!fs.existsSync(abs)) {
      zoneStats[zone] = zInfo;
      continue;
    }
    zInfo.present = true;
    const text = fs.readFileSync(abs, "utf8");
    const parsed = parsePetkaPartsCsv(text);
    if (parsed.errors?.length) {
      parseErrors.push(
        ...parsed.errors.map((e) => ({
          zone,
          line: e.line,
          sku: e.sku,
          message: e.message,
        })),
      );
    }
    for (const draft of parsed.drafts) {
      const sku = draft.sku;
      total++;
      zInfo.total_parts++;
      const hasPrice = draft.oem_price != null;
      if (hasPrice) {
        withPrice++;
        zInfo.with_oem_price++;
      }

      if (FORCE_EMPTY_PRICE_SKUS.has(sku)) {
        if (!hasPrice) forcedOk.push(sku);
        else forcedBad.push(sku);
      }

      const notes = draft.notes;
      const priceNote = draft.price_note;
      if (isPetkaPriceVerified(notes, priceNote)) {
        verified.push(sku);
        zInfo.petka_verified++;
      } else if (needsPetkaPriceVerify(notes, priceNote)) {
        needs.push(sku);
        zInfo.needs_verify++;
      }
    }
    zoneStats[zone] = zInfo;
  }

  const vTrunc = truncateSkuList(verified);
  const nTrunc = truncateSkuList(needs);

  return {
    domain_source: helpers.source,
    zones: zoneStats,
    total_parts: total,
    with_oem_price: withPrice,
    petka_verified: verified.length,
    needs_verify: needs.length,
    verified_skus: vTrunc.list,
    needs_verify_skus: nTrunc.list,
    ...(vTrunc.truncated || nTrunc.truncated
      ? { skus_truncated: true, skus_cap: SKU_LIST_CAP }
      : {}),
    forced_empty_ok: forcedOk,
    ...(forcedBad.length ? { forced_empty_violations: forcedBad } : {}),
    ...(parseErrors.length
      ? { parse_errors: parseErrors.slice(0, 20), parse_error_count: parseErrors.length }
      : {}),
  };
}

function countFilledPrices() {
  const info = {
    path: rel(PRICES_TSV),
    present: false,
    rows: 0,
    filled_price_count: 0,
    filled_skus: [],
  };
  if (!fs.existsSync(PRICES_TSV)) return info;
  info.present = true;
  const text = fs.readFileSync(PRICES_TSV, "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length === 0) return info;
  const header = lines[0].split("\t").map((h) => h.trim());
  const iSku = header.indexOf("sku");
  const iPrice = header.indexOf("oem_price");
  if (iSku < 0 || iPrice < 0) {
    info.error = "invalid_tsv_header";
    return info;
  }
  for (const line of lines.slice(1)) {
    const cols = line.split("\t");
    const sku = (cols[iSku] || "").trim();
    if (!sku) continue;
    info.rows++;
    const price = cols[iPrice] || "";
    if (isNonEmptyPrice(price)) {
      info.filled_price_count++;
      info.filled_skus.push(sku);
    }
  }
  return info;
}

function countPetkaInboxPending() {
  const info = { path: rel(PETKA_INBOX), present: false, pending_count: 0, files: [] };
  if (!fs.existsSync(PETKA_INBOX)) return info;
  info.present = true;
  const names = fs
    .readdirSync(PETKA_INBOX, { withFileTypes: true })
    .filter((d) => d.isFile())
    .map((d) => d.name)
    .filter((name) => {
      if (name.startsWith(".") || name.startsWith("_")) return false;
      if (/^readme/i.test(name)) return false;
      const ext = path.extname(name).toLowerCase();
      return ext === ".txt" || ext === ".tsv" || ext === ".csv";
    });
  info.pending_count = names.length;
  info.files = names;
  return info;
}

function countLocatorInboxPending() {
  const info = { path: rel(LOCATOR_INBOX), present: false, pending_count: 0, files: [] };
  if (!fs.existsSync(LOCATOR_INBOX)) return info;
  info.present = true;
  const files = [];
  const EXTS = new Set(OVERVIEW_EXTS);

  for (const ent of fs.readdirSync(LOCATOR_INBOX, { withFileTypes: true })) {
    if (ent.name === "done") continue;
    if (ent.name.startsWith(".") || ent.name.startsWith("_")) continue;
    const abs = path.join(LOCATOR_INBOX, ent.name);
    if (ent.isFile()) {
      const ext = path.extname(ent.name).toLowerCase();
      if (EXTS.has(ext)) files.push(ent.name);
      continue;
    }
    if (ent.isDirectory() && ZONES.includes(ent.name.toLowerCase())) {
      for (const child of fs.readdirSync(abs, { withFileTypes: true })) {
        if (!child.isFile()) continue;
        if (child.name.startsWith(".") || child.name.startsWith("_")) continue;
        const ext = path.extname(child.name).toLowerCase();
        if (EXTS.has(ext)) files.push(`${ent.name}/${child.name}`);
      }
    }
  }
  info.pending_count = files.length;
  info.files = files;
  return info;
}

function scanOverviews() {
  const zones = {};
  let exists_count = 0;
  for (const zone of ZONES) {
    const shotsDir = path.join(root, "data", "petka", zone, "shots");
    let found = null;
    for (const ext of OVERVIEW_EXTS) {
      const abs = path.join(shotsDir, `overview${ext}`);
      if (fs.existsSync(abs)) {
        found = {
          overview_exists: true,
          overview_path: rel(abs),
          overview_ext: ext,
        };
        break;
      }
    }
    if (!found) {
      found = {
        overview_exists: false,
        overview_path: `data/petka/${zone}/shots/overview.png`,
        overview_ext: null,
      };
    }
    if (found.overview_exists) exists_count++;
    zones[zone] = found;
  }
  return { zones, exists_count, all_present: exists_count === ZONES.length };
}

function runPowershell(script) {
  try {
    const out = execFileSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
      {
        cwd: root,
        encoding: "utf8",
        windowsHide: true,
        timeout: 15000,
        maxBuffer: 2 * 1024 * 1024,
      },
    );
    return { ok: true, stdout: String(out || "").trim() };
  } catch (err) {
    return {
      ok: false,
      error: err && err.message ? String(err.message) : String(err),
      stdout: err && err.stdout ? String(err.stdout).trim() : "",
      stderr: err && err.stderr ? String(err.stderr).trim() : "",
    };
  }
}

function queryEtka7() {
  // Read-only: MainWindowTitle via Get-Process. Do NOT activate / focus / kill.
  const ps = `
$ErrorActionPreference = 'SilentlyContinue'
$procs = @(Get-Process -Name 'Etka7' -ErrorAction SilentlyContinue)
if (-not $procs -or $procs.Count -eq 0) {
  @{ running = $false; count = 0; titles = @(); processes = @() } | ConvertTo-Json -Compress -Depth 4
  exit 0
}
$items = @()
$titles = @()
foreach ($p in $procs) {
  $t = [string]$p.MainWindowTitle
  if ($null -eq $t) { $t = '' }
  $titles += $t
  $items += @{ id = $p.Id; name = $p.ProcessName; main_window_title = $t }
}
@{
  running = $true
  count = $procs.Count
  titles = $titles
  main_window_title = ($titles | Where-Object { $_ -ne '' } | Select-Object -First 1)
  processes = $items
} | ConvertTo-Json -Compress -Depth 5
`.trim();

  const res = runPowershell(ps);
  if (!res.ok) {
    return { status: "unknown", error: res.error || res.stderr || "powershell_failed" };
  }
  if (!res.stdout) {
    return { status: "unknown", error: "empty_powershell_output" };
  }
  try {
    const data = JSON.parse(res.stdout);
    const titles = Array.isArray(data.titles) ? data.titles : [];
    const titled = titles.find((t) => t) || null;
    return {
      status: data.running ? "running" : "not_running",
      running: Boolean(data.running),
      count: Number(data.count) || 0,
      main_window_title:
        data.main_window_title != null && data.main_window_title !== ""
          ? data.main_window_title
          : titled,
      titles,
      processes: Array.isArray(data.processes) ? data.processes : [],
    };
  } catch (e) {
    return {
      status: "unknown",
      error: `json_parse: ${e.message}`,
      raw: res.stdout.slice(0, 500),
    };
  }
}

function queryWatchProcesses() {
  // Hardcode PS array — JSON '[' breaks when passed via -Command (type cast).
  const ps = `
$ErrorActionPreference = 'SilentlyContinue'
$markers = @('watch-petka-inbox','watch-locator-inbox','watch-locator-shots','watch-petka-handcopy')
$procs = @()
try {
  $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction Stop)
} catch {
  try {
    $procs = @(Get-WmiObject Win32_Process -Filter "Name = 'node.exe'" -ErrorAction Stop)
  } catch {
    Write-Output '__QUERY_FAILED__'
    Write-Output $_.Exception.Message
    exit 0
  }
}
$hits = @()
foreach ($p in $procs) {
  $cl = [string]$p.CommandLine
  if ([string]::IsNullOrEmpty($cl)) { continue }
  foreach ($m in $markers) {
    if ($cl -like ('*' + $m + '*')) {
      $hits += @{
        marker = $m
        pid = [int]$p.ProcessId
        command_line = $cl
      }
      break
    }
  }
}
@{ ok = $true; hits = $hits } | ConvertTo-Json -Compress -Depth 6
`.trim();

  const res = runPowershell(ps);
  const watches = {};
  for (const m of WATCH_MARKERS) {
    watches[m] = { running: false, pids: [], command_lines: [] };
  }

  if (!res.ok) {
    const raw = res.stderr || res.error || "powershell_failed";
    const short = String(raw).split(/\r?\n/).find((l) => l.trim()) || "powershell_failed";
    return { status: "unknown", error: short.slice(0, 240), watches };
  }
  if (res.stdout.includes("__QUERY_FAILED__")) {
    const msg = res.stdout.replace("__QUERY_FAILED__", "").trim() || "cim_wmi_failed";
    return { status: "unknown", error: msg.split(/\r?\n/).find((l) => l.trim()).slice(0, 240), watches };
  }
  if (!res.stdout) {
    return { status: "unknown", error: "empty_powershell_output", watches };
  }

  try {
    const data = JSON.parse(res.stdout);
    const hits = Array.isArray(data.hits)
      ? data.hits
      : data.hits
        ? [data.hits]
        : [];
    for (const h of hits) {
      const marker = h.marker;
      if (!watches[marker]) {
        watches[marker] = { running: false, pids: [], command_lines: [] };
      }
      watches[marker].running = true;
      if (h.pid != null) watches[marker].pids.push(Number(h.pid));
      if (h.command_line) watches[marker].command_lines.push(String(h.command_line));
    }
    return { status: "ok", watches };
  } catch (e) {
    return {
      status: "unknown",
      error: `json_parse: ${e.message}`,
      raw: res.stdout.slice(0, 500),
      watches,
    };
  }
}

async function buildStatus() {
  const prices = countFilledPrices();
  const petkaInbox = countPetkaInboxPending();
  const locatorInbox = countLocatorInboxPending();
  const overviews = scanOverviews();
  const etka7 = queryEtka7();
  const watchQuery = queryWatchProcesses();
  const domainHelpers = await loadDomainCsvHelpers();
  const csvPriceVerify = scanCsvPriceVerify(domainHelpers);

  const noteParts = [
    "Read-only process/title + repo files. Never activates PETKA / Etka7 GUI.",
    "fuel-filter/tire-fl oem_price must stay empty (forced_empty_ok).",
  ];
  if (csvPriceVerify.error) {
    noteParts.push(`csv_price_verify unavailable: ${csvPriceVerify.error}`);
  }

  return {
    checked_at: new Date().toISOString(),
    task: "petka-background-status",
    etka7,
    petka_prices: {
      path: prices.path,
      present: prices.present,
      rows: prices.rows,
      filled_price_count: prices.filled_price_count,
      filled_skus: prices.filled_skus,
      ...(prices.error ? { error: prices.error } : {}),
    },
    csv_price_verify: csvPriceVerify,
    inboxes: {
      petka_inbox: {
        path: petkaInbox.path,
        present: petkaInbox.present,
        pending_count: petkaInbox.pending_count,
        files: petkaInbox.files,
      },
      locator_inbox: {
        path: locatorInbox.path,
        present: locatorInbox.present,
        pending_count: locatorInbox.pending_count,
        files: locatorInbox.files,
      },
    },
    overviews: {
      exists_count: overviews.exists_count,
      all_present: overviews.all_present,
      zones: overviews.zones,
    },
    watches: {
      query_status: watchQuery.status,
      ...(watchQuery.error ? { error: watchQuery.error } : {}),
      ...(watchQuery.raw ? { raw: watchQuery.raw } : {}),
      processes: watchQuery.watches,
    },
    note: noteParts.join(" "),
  };
}

function printSummary(status) {
  const lines = [];
  lines.push("=== PETKA / locator status ===");
  lines.push(`checked_at: ${status.checked_at}`);

  const e = status.etka7;
  if (e.status === "unknown") {
    lines.push(`Etka7: unknown (${e.error || "n/a"})`);
  } else if (!e.running) {
    lines.push("Etka7: not running");
  } else {
    const title = e.main_window_title || "(no MainWindowTitle)";
    lines.push(`Etka7: running x${e.count}  title="${title}"`);
  }

  const p = status.petka_prices;
  lines.push(
    `petka-prices.tsv: ${p.present ? `present  filled=${p.filled_price_count}/${p.rows}` : "missing"}`,
  );

  const csv = status.csv_price_verify;
  if (csv?.error) {
    lines.push(`csv: unavailable (${csv.error})`);
  } else if (csv) {
    lines.push(
      `csv: verified=${csv.petka_verified} needs_verify=${csv.needs_verify} filled=${csv.with_oem_price}/${csv.total_parts}` +
        (csv.forced_empty_ok?.length
          ? ` forced_empty_ok=${csv.forced_empty_ok.length}`
          : ""),
    );
  }

  const pi = status.inboxes.petka_inbox;
  const li = status.inboxes.locator_inbox;
  lines.push(
    `petka-inbox pending: ${pi.present ? pi.pending_count : "dir missing"}`,
  );
  lines.push(
    `locator-inbox pending: ${li.present ? li.pending_count : "dir missing"}`,
  );

  const ov = status.overviews;
  const bits = ZONES.map((z) => `${z}=${ov.zones[z].overview_exists ? "yes" : "no"}`);
  lines.push(`overviews: ${ov.exists_count}/3  (${bits.join(", ")})`);

  const w = status.watches;
  if (w.query_status === "unknown") {
    lines.push(`watches: unknown (${w.error || "n/a"})`);
  } else {
    const parts = WATCH_MARKERS.map((m) => {
      const info = w.processes[m];
      return `${m}=${info && info.running ? "on" : "off"}`;
    });
    lines.push(`watches: ${parts.join("  ")}`);
  }

  lines.push(`wrote: ${rel(OUT)}`);
  console.log(lines.join("\n"));
}

async function main() {
  fs.mkdirSync(LOCAL, { recursive: true });
  const status = await buildStatus();
  fs.writeFileSync(OUT, `${JSON.stringify(status, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(status, null, 2));
  console.log("");
  printSummary(status);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});