/**
 * Bounded OBD connection IPC: persistent ATRV monitor + list/select/disconnect.
 * Renderer never supplies a COM path. Live sessions read selected identity from here.
 */
import { createCanFrameBatch, createCanPcapWriter } from "./internal-can-data.mjs";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import { buildDeviceRegistry, readKnownDevices } from "./obd-device-registry.mjs";
import { resolvePythonCandidates } from "./offline-diagnostics.mjs";
import { liveBlocked, validateSessionRequest } from "./read-only-session.mjs";
import {
  ATRV_INTERVAL_MS,
  MONITOR_GRACEFUL_MS,
  MONITOR_HANDSHAKE_MS,
  MONITOR_KILL_WAIT_MS,
  MONITOR_LIVENESS_MS,
  VOLTAGE_FRESH_MS,
  acceptMonitorReading,
  createTransportGate,
  formatHeaderVoltage,
  isTransportFault,
  nextRetryDelayMs,
  parseVoltageVolts,
  voltageFresh,
} from "../src/obd-connection-logic.mjs";

export const CONNECTION_CHANNEL = "diagnostics:connection";
export {
  ATRV_INTERVAL_MS,
  VOLTAGE_FRESH_MS,
  acceptMonitorReading,
  createTransportGate,
  formatHeaderVoltage,
  nextRetryDelayMs,
  parseVoltageVolts,
};

const ACTIONS = new Set(["list", "select", "configure", "connect", "voltage", "disconnect", "clear", "status", "record-start", "record-stop", "new-batch", "save-result"]);
const DEVICE_RE = /^(?:bt:[0-9A-F]{12}|vnci:[0-9]{1,16})$/;
const MODELS = new Set(["vLinker", "OBDLink MX+", "VNCI"]);
const MAX_JSON = 16 * 1024;
const LIST_TIMEOUT_MS = 20_000;
const MONITOR_LINE_CAP = 64 * 1024;
const FLAGS = Object.freeze({ executionEnabled: false, liveVerified: false, writePayload: null });

function isPlain(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

export function fail(error, extra = {}) {
  return { ok: false, error, ...FLAGS, ...extra };
}

export function validateConnectionRequest(req) {
  if (!isPlain(req)) return fail("malformed_request");
  if (!ACTIONS.has(req.action)) return fail("invalid_action");
  const allowed =
    req.action === "configure" ? ["action", "purpose", "canNetwork"] :
    req.action === "select" || req.action === "connect" || req.action === "voltage"
      ? ["action", "deviceId", "model"]
      : ["action"];
  const extra = Object.keys(req).filter((k) => !allowed.includes(k));
  if (extra.length) return fail("forbidden_field", { fields: extra });
  if (req.deviceId != null && (typeof req.deviceId !== "string" || !DEVICE_RE.test(req.deviceId))) {
    return fail("malformed_device_id");
  }
  if (req.model != null && (typeof req.model !== "string" || !MODELS.has(req.model))) {
    return fail("malformed_model");
  }
  if (req.purpose != null && !["diagnostic", "internal"].includes(req.purpose)) return fail("invalid_purpose");
  if (req.canNetwork != null && !["drive", "chassis", "comfort", "crash", "adas"].includes(req.canNetwork)) return fail("invalid_can_network");
  if (req.action === "configure" && req.purpose == null && req.canNetwork == null) return fail("missing_settings");
  try {
    if (Buffer.byteLength(JSON.stringify(req), "utf8") > MAX_JSON) return fail("request_too_large");
  } catch {
    return fail("malformed_request");
  }
  return null;
}

function persistPath(repoRoot, env) {
  if (env.PORSCHE981_CONNECTION_STATE) return env.PORSCHE981_CONNECTION_STATE;
  return path.join(repoRoot, ".local", "obd-connection.json");
}

function loadState(file) {
  const defaults = { deviceId: null, model: null, purpose: "diagnostic", canNetwork: null };
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!isPlain(raw)) return defaults;
    const deviceId = typeof raw.deviceId === "string" && DEVICE_RE.test(raw.deviceId) ? raw.deviceId : null;
    const model = MODELS.has(raw.model) ? raw.model : null;
    return { deviceId, model, purpose: raw.purpose === "internal" ? "internal" : "diagnostic",
      canNetwork: ["drive", "chassis", "comfort", "crash", "adas"].includes(raw.canNetwork) ? raw.canNetwork : null };
  } catch {
    return defaults;
  }
}

function saveState(file, state) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(state, null, 2), "utf8");
}

function spawnPython(opts, args, payload, timeoutMs) {
  const env = { ...opts.envIn, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  delete env.PORSCHE981_SERIAL;
  delete env.PORSCHE981_SERIAL_PORT;
  const candidates = opts.spawnFn ? [{ exe: "stub", prefix: [] }] : resolvePythonCandidates(env);
  const spawnFn = opts.spawnFn || spawn;
  return new Promise((resolve) => {
    const tryOne = (idx) => {
      if (idx >= candidates.length) {
        resolve({ ok: false, error: "python_runtime_missing", ...FLAGS });
        return;
      }
      const { exe, prefix } = candidates[idx];
      let child;
      try {
        child = spawnFn(exe, opts.spawnFn ? args : [...prefix, ...args], {
          cwd: opts.repoRoot,
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          shell: false,
        });
      } catch {
        tryOne(idx + 1);
        return;
      }
      let stdout = "";
      let stderr = "";
      let settled = false;
      let failure = null;
      let killWatchdog;
      opts.children?.add(child);
      const done = (doc) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        clearTimeout(killWatchdog);
        resolve(doc);
      };
      const terminate = (error) => {
        if (failure) return;
        failure = error;
        try {
          child.kill();
        } catch {
          /* */
        }
        killWatchdog = setTimeout(() => done(fail(error)), 1500);
      };
      const timer = setTimeout(() => terminate("timeout"), timeoutMs);
      child.on("error", (err) => {
        if (err && (err.code === "ENOENT" || err.code === "EINVAL") && !opts.spawnFn) {
          opts.children?.delete(child);
          settled = true;
          clearTimeout(timer);
          tryOne(idx + 1);
          return;
        }
        terminate("spawn_failed");
      });
      child.stdout?.on("data", (c) => {
        stdout += c.toString("utf8");
        if (stdout.length > 512 * 1024) terminate("output_cap");
      });
      child.stderr?.on("data", (c) => {
        stderr = (stderr + c.toString("utf8")).slice(-32 * 1024);
      });
      child.on("close", (code) => {
        opts.children?.delete(child);
        if (failure) {
          done(fail(failure));
          return;
        }
        const line = stdout.trim().split("\n").filter(Boolean).pop() || "";
        let doc;
        try {
          doc = JSON.parse(line);
        } catch {
          done(fail("malformed_output", { code, stderr: stderr.slice(0, 200) }));
          return;
        }
        if (!isPlain(doc)) {
          done(fail("protocol_error"));
          return;
        }
        done({ ...doc, ok: code === 0 && doc.ok === true, ...FLAGS, liveVerified: false, writePayload: null });
      });
      try {
        child.stdin.write(payload, "utf8");
      } catch {
        terminate("stdin_closed");
      }
    };
    tryOne(0);
  });
}

function acceptMonitorLine(doc, ctx) {
  return acceptMonitorReading(doc, ctx);
}

export function createObdConnectionManager(opts) {
  const envIn = opts.env || process.env;
  const file = persistPath(opts.repoRoot, envIn);
  const registryFile = file.replace(/\.json$/i, "") + ".devices.json";
  let knownDevices = readKnownDevices(registryFile);
  let deviceRegistry = buildDeviceRegistry({ known: knownDevices }).registry;
  const gate = opts.gate || createTransportGate();
  const listFn = opts.listFn;
  const nowFn = opts.now || Date.now;
  const freshMs = opts.freshMs ?? VOLTAGE_FRESH_MS;
  const setT = opts.setTimeoutFn || setTimeout;
  const clearT = opts.clearTimeoutFn || clearTimeout;
  const children = new Set();
  const spawnOpts = { ...opts, envIn, spawnFn: opts.spawnFn, children };

  let selected = loadState(file);
  let connected = false;
  let wantConnected = false;
  let lastList = { devices: [], errors: [] };
  let reading = null;
  let stopped = false;
  let monitorChild = null;
  let monitorToken = null;
  let ingestGen = 0;
  let linkState = "idle";
  let retryTimer = null;
  let retryFails = 0;
  let lastFatal = null;
  let dispatchReserved = false;
  let pausedForSession = false;
  let watchTimer = null;
  let opChain = Promise.resolve();
  let pendingMutations = 0;
  let stopSession = null;
  let monitorStarts = 0, omittedMonitorEvents = 0;
  const monitorEvents = [];
  let frameBatch = createCanFrameBatch();
  let recorder = null;
  let recordingGaps = [], omittedRecordingGaps = 0;
  let recordingResult = null;
  const handshakeMs = opts.handshakeMs ?? MONITOR_HANDSHAKE_MS;
  const livenessMs = opts.livenessMs ?? MONITOR_LIVENESS_MS;
  const gracefulMs = opts.gracefulMs ?? MONITOR_GRACEFUL_MS;
  const killWaitMs = opts.killWaitMs ?? MONITOR_KILL_WAIT_MS;

  function monitorEvent(type, details = {}) {
    const event = { type, at: nowFn(), ...details };
    monitorEvents.push(event);
    if (monitorEvents.length > 32) { monitorEvents.shift(); omittedMonitorEvents++; }
    // Optional local acceptance observer. Ordinary sessions do not persist logs.
    try { opts.onMonitorEvent?.(event); } catch { /* logging cannot break ownership */ }
  }

  function selectedId() {
    return selected.deviceId;
  }

  function modelForSelection(request, hit) {
    return request.model || (MODELS.has(hit.brand) ? hit.brand : null)
      || (request.deviceId === selected.deviceId ? selected.model : null);
  }

  function clearReading() {
    reading = null;
  }

  function applyReading(volts, source, at) {
    if (source !== "atrv" && source !== "d-pdu-vbatt") {
      clearReading();
      return;
    }
    if (typeof volts !== "number" || !Number.isFinite(volts) || volts < 6 || volts > 20) {
      clearReading();
      return;
    }
    reading = { volts, source, at: at ?? nowFn() };
  }

  function voltageView() {
    const now = nowFn();
    if (!reading || !voltageFresh(reading.at, now, freshMs)) {
      if (reading) reading = null;
      return { voltageVolts: null, voltageSource: null, voltageAt: null };
    }
    return { voltageVolts: reading.volts, voltageSource: reading.source, voltageAt: reading.at };
  }

  function snapshot(extra = {}) {
    const v = voltageView();
    const diagnostic = pausedForSession || gate.owner() === "session";
    if (["disconnecting", "close_failed"].includes(linkState) && !monitorChild && !gate.owner() && !dispatchReserved) {
      linkState = "idle";
      lastFatal = null;
    }
    const state = ["disconnecting", "close_failed"].includes(linkState) ? linkState : diagnostic ? "diagnostic" : linkState;
    return {
      ok: true,
      ...FLAGS,
      selectedDeviceId: selected.deviceId,
      model: selected.model,
      purpose: selected.purpose,
      canNetwork: selected.canNetwork,
      internal: selected.purpose === "internal" ? frameBatch.view() : null,
      recording: recorder ? { active: true, file: recorder.file, startedAt: recorder.startedAt, frameCount: recorder.count } : recordingResult,
      internalSupported: selected.model === "OBDLink MX+" && selected.canNetwork === "drive",

      connected,
      linkState: state,
      pairingOk: lastList.devices.some((d) => d.id === selected.deviceId && d.paired),
      commOk: connected,
      connectionError: lastFatal,
      monitorDiagnostics: { starts: monitorStarts, events: monitorEvents.slice(), omittedEvents: omittedMonitorEvents },
      devices: lastList.devices,
      deviceRegistry,
      listErrors: lastList.errors || [],
      voltageLabel: formatHeaderVoltage(v.voltageVolts),
      atrvIntervalMs: ATRV_INTERVAL_MS,
      voltageFreshMs: freshMs,
      ...v,
      ...extra,
    };
  }

  function persist() {
    saveState(file, selected);
  }

  function enqueue(fn, allowBusy = false) {
    pendingMutations += 1;
    const execute = async () => {
      try {
        if (stopped) return fail("connection_closed");
        if (sessionBusy() && !allowBusy) return fail("busy");
        return await fn();
      } finally {
        pendingMutations -= 1;
      }
    };
    const run = opChain.then(execute, execute);
    opChain = run.then(
      () => {},
      () => {},
    );
    return run;
  }

  function clearWatch() {
    if (watchTimer) clearT(watchTimer);
    watchTimer = null;
  }

  function cancelRetry() {
    if (retryTimer) clearT(retryTimer);
    retryTimer = null;
  }

  function sessionBusy() {
    return gate.owner() === "session" || dispatchReserved;
  }

  function noteLiveAtrv(adapter, mode) {
    if (mode !== "live") return;
    const source = adapter?.voltageSource === "d-pdu-vbatt" ? "d-pdu-vbatt" : "atrv";
    const volts = source === "d-pdu-vbatt" ? adapter.volts : parseVoltageVolts(adapter?.atrv);
    if (volts == null) return;
    applyReading(volts, source);
  }

  async function listDevices() {
    try {
      const doc = listFn
        ? await listFn()
        : envIn.PORSCHE981_CONNECTION_FIXTURE
          ? JSON.parse(fs.readFileSync(envIn.PORSCHE981_CONNECTION_FIXTURE, "utf8"))
          : await spawnPython(spawnOpts, ["-m", "scripts.diagnostics.connection"], JSON.stringify({ action: "list" }) + "\n", LIST_TIMEOUT_MS);
      const devices = Array.isArray(doc.devices) ? doc.devices.filter((d) => d && DEVICE_RE.test(d.id) &&
        d.available === true && (MODELS.has(d.brand) || d.brand === "unresolved")) : [];
      lastList = { devices, errors: Array.isArray(doc.errors) ? doc.errors : doc.error ? [doc.error] : [] };
      const registered = buildDeviceRegistry({ devices, host: doc.host || {}, known: knownDevices });
      deviceRegistry = registered.registry;
      knownDevices = registered.known;
      try {
        fs.mkdirSync(path.dirname(registryFile), { recursive: true });
        fs.writeFileSync(registryFile, JSON.stringify({ version: 1, devices: knownDevices }, null, 2), "utf8");
      } catch { lastList.errors.push("device-registry-save-failed"); }
      if (!devices.some((d) => d.id === selected.deviceId && d.available) && !wantConnected) {
        connected = false;
        clearReading();
      }
      return snapshot({ ok: true });
    } catch {
      lastList = { devices: [], errors: ["list-failed"] };
      deviceRegistry = buildDeviceRegistry({ known: knownDevices }).registry;
      if (!wantConnected) clearReading();
      return fail("list_failed", snapshot({ ok: false }));
    }
  }

  function waitUntilClosed(child, maxMs) {
    return new Promise((resolve) => {
      if (!child || child.exitCode != null || child._exited) {
        resolve(true);
        return;
      }
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        clearT(timer);
        resolve(ok);
      };
      const onClose = () => finish(true);
      child.once("close", onClose);
      const timer = setT(() => {
        child.removeListener("close", onClose);
        finish(false);
      }, maxMs);
    });
  }

  async function stopMonitorProcess() {
    const child = monitorChild;
    if (!child) return { ok: true };
    monitorEvent("stop-requested", { generation: child._monitorGeneration, pid: child.pid ?? null });
    ingestGen += 1;
    clearWatch();
    try {
      child.stdin.write(JSON.stringify({ action: "stop" }) + "\n", "utf8");
    } catch {
      /* */
    }
    if (await waitUntilClosed(child, gracefulMs)) return { ok: true };
    try {
      monitorEvent("forced-kill", { generation: child._monitorGeneration, reason: "graceful-close-timeout" });
      child.kill();
    } catch {
      /* */
    }
    if (await waitUntilClosed(child, killWaitMs)) return { ok: true };
    monitorEvent("close-timeout", { generation: child._monitorGeneration });
    return { ok: false, error: "monitor_close_timeout" };
  }

  function armWatch(ms, reason) {
    clearWatch();
    watchTimer = setT(() => {
      watchTimer = null;
      if (stopped || !monitorChild) return;
      monitorEvent("watchdog", { generation: monitorChild._monitorGeneration, reason,
        lastAcceptedAt: monitorChild._lastAcceptedAt ?? null });
      lastFatal = reason;
      connected = false;
      clearReading();
      try {
        monitorChild.kill();
      } catch {
        /* retain until close */
      }
    }, ms);
  }

  function scheduleRetry(error) {
    if (stopped || !wantConnected || pausedForSession) return;
    if (!isTransportFault(error)) {
      wantConnected = false;
      connected = false;
      linkState = "idle";
      clearReading();
      return;
    }
    connected = false;
    clearReading();
    linkState = "reconnecting";
    retryFails += 1;
    const delay = opts.retryDelayMs ? opts.retryDelayMs(retryFails) : nextRetryDelayMs(retryFails);
    monitorEvent("retry-scheduled", { reason: String(error).slice(0, 160), delayMs: delay });
    cancelRetry();
    retryTimer = setT(() => {
      retryTimer = null;
      if (stopped || !wantConnected || pausedForSession) return;
      void startMonitor();
    }, delay);
  }

  function ingestLine(doc, gen, child) {
    if (doc?.type === "stopped") child._stopReported = true;
    if (stopped || gen !== ingestGen) return;
    if (doc?.type === "frames") {
      if (selected.purpose !== "internal" || doc.deviceId !== selected.deviceId || doc.canNetwork !== selected.canNetwork || doc.simulation !== false) return;
      try {
        frameBatch.ingest(doc.frames);
        if (recorder) {
          try { recorder.append(doc.frames); }
          catch (error) { finishRecording("recording-write-failed"); recordingResult.error = String(error.code || error.message || error); }
        }
        armWatch(livenessMs, "timeout");
        child._lastAcceptedAt = nowFn();
      } catch (error) {
        lastFatal = String(error.message || error); wantConnected = false; connected = false;
        finishRecording("recording-or-stream-failed");
        try { child.kill(); } catch { /* keep ownership */ }
      }
      return;
    }
    const acc = acceptMonitorLine(doc, { deviceId: selected.deviceId, now: nowFn(), freshMs });
    if (acc.ignore) return;
    if (acc.reject) {
      if (child._lastReject !== acc.reject) monitorEvent("reading-rejected", { generation: gen, reason: acc.reject,
        sampleAt: typeof doc.at === "string" ? doc.at.slice(0, 64) : typeof doc.at === "number" && Number.isFinite(doc.at) ? doc.at : null });
      child._lastReject = acc.reject;
      return;
    }
    if (acc.fatal) {
      monitorEvent("reported-error", { generation: gen, reason: acc.error.slice(0, 160) });
      lastFatal = acc.error;
      connected = false;
      clearReading();
      if (!isTransportFault(acc.error)) {
        wantConnected = false;
        linkState = "idle";
      }
      try {
        child.kill();
      } catch {
        /* */
      }
      return;
    }
    applyReading(acc.volts, acc.source, acc.at);
    child._lastAcceptedAt = nowFn();
    connected = true;
    linkState = "connected";
    retryFails = 0;
    lastFatal = null;
    armWatch(livenessMs, "timeout");
  }

  function bindMonitorChild(child, gen, token, id) {
    monitorChild = child;
    monitorToken = token;
    children.add(child);
    child._monitorGeneration = gen;
    monitorStarts++;
    monitorEvent("started", { generation: gen, pid: child.pid ?? null });
    lastFatal = null;
    let buf = "";
    let outputFailed = false, stderrBytes = 0;
    const decoder = new StringDecoder("utf8");
    const overflow = () => {
      buf = ""; outputFailed = true;
      lastFatal = "output_cap"; connected = false; clearReading();
      monitorEvent("output-limit", { generation: gen });
      try { child.kill(); } catch { /* retain lock until close */ }
    };
    child.stdout?.on("data", (c) => {
      if (outputFailed) return;
      buf += decoder.write(c);
      const parts = buf.split("\n");
      buf = parts.pop() || "";
      for (const line of parts) {
        if (Buffer.byteLength(line, "utf8") > MONITOR_LINE_CAP) { overflow(); return; }
        if (!line.trim()) continue;
        try {
          ingestLine(JSON.parse(line), gen, child);
        } catch {
          /* reject malformed */
        }
      }
      if (Buffer.byteLength(buf, "utf8") > MONITOR_LINE_CAP) overflow();
    });
    // Always drain stderr: an unread pipe can block an otherwise healthy worker.
    child.stderr?.on("data", (c) => { stderrBytes += c.length; });
    child.on("error", (err) => {
      if (child._fallback || gen !== ingestGen) return;
      if (err && (err.code === "ENOENT" || err.code === "EINVAL")) return;
      lastFatal = "spawn_failed";
      connected = false;
      clearReading();
      try {
        child.kill();
      } catch {
        /* */
      }
    });
    child.on("close", (code, signal) => {
      monitorEvent("closed", { generation: gen, pid: child.pid ?? null, code: code ?? null,
        signal: signal ?? null, reason: lastFatal, stopReported: child._stopReported === true,
        lastAcceptedAt: child._lastAcceptedAt ?? null, stderrBytes });
      child._exited = true;
      children.delete(child);
      if (child._fallback) return;
      if (monitorChild === child) {
        monitorChild = null;
        monitorToken = null;
      }
      clearWatch();
      if (gate.owner() === token) gate.release(token);
      if (stopped || pausedForSession) return;
      if (gen !== ingestGen) return;
      if (selected.purpose === "internal") {
        frameBatch.interrupt(lastFatal || "port-io");
        if (recorder) {
          recordingGaps.push({ at: new Date().toISOString(), reason: lastFatal || "port-io" });
          if (recordingGaps.length > 128) { recordingGaps.shift(); omittedRecordingGaps++; }
        }
      }
      if (!wantConnected) finishRecording("monitor-ended");
      if (wantConnected) scheduleRetry(lastFatal || "port-io");
    });
    try {
      child.stdin.write(JSON.stringify({ action: "monitor", deviceId: id, purpose: selected.purpose, canNetwork: selected.canNetwork }) + "\n", "utf8");
    } catch {
      lastFatal = "stdin_closed";
      try {
        child.kill();
      } catch {
        /* */
      }
    }
    armWatch(handshakeMs, "timeout");
  }

  async function startMonitor() {
    if (stopped || !wantConnected || pausedForSession || monitorChild) return;
    const id = selected.deviceId;
    if (!id) return;
    if (gate.owner() && !String(gate.owner()).startsWith("monitor")) return;
    const gen = ++ingestGen;
    const token = `monitor:${gen}`;
    if (!gate.tryAcquire(token)) return;
    linkState = "connecting";
    const env = { ...envIn, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
    delete env.PORSCHE981_SERIAL;
    delete env.PORSCHE981_SERIAL_PORT;
    const spawnFn = opts.monitorSpawnFn || opts.spawnFn || spawn;
    const injected = Boolean(opts.monitorSpawnFn || opts.spawnFn);
    const candidates = opts.pythonCandidates || (injected ? [{ exe: "stub", prefix: [] }] : resolvePythonCandidates(env));

    const tryOne = (idx) => {
      if (stopped || gen !== ingestGen) {
        if (gate.owner() === token) gate.release(token);
        return;
      }
      if (idx >= candidates.length) {
        if (gate.owner() === token) gate.release(token);
        lastFatal = "python_runtime_missing";
        scheduleRetry("python_runtime_missing");
        return;
      }
      const { exe, prefix } = candidates[idx];
      let child;
      try {
        child = spawnFn(exe, injected ? ["-m", "scripts.diagnostics.connection"] : [...prefix, "-m", "scripts.diagnostics.connection"], {
          cwd: opts.repoRoot,
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          shell: false,
        });
      } catch {
        tryOne(idx + 1);
        return;
      }
      child.once("error", (err) => {
        if (!(err && (err.code === "ENOENT" || err.code === "EINVAL"))) return;
        if (idx + 1 >= candidates.length) return;
        child._fallback = true;
        if (monitorChild === child) {
          children.delete(child);
          monitorChild = null;
          monitorToken = null;
          clearWatch();
        }
        tryOne(idx + 1);
      });
      bindMonitorChild(child, gen, token, id);
    };
    tryOne(0);
  }

  async function beginConnect(model) {
    if (stopped) return fail("connection_closed");
    if (sessionBusy()) return fail("busy");
    if (["disconnecting", "close_failed"].includes(linkState) && (monitorChild || gate.owner())) return fail("port_release_pending", snapshot({ ok: false }));
    const id = selected.deviceId;
    if (!id) return fail("device_not_selected");
    if (!lastList.devices.some((d) => d.id === id && d.available)) return fail("device_port_unavailable", snapshot({ ok: false }));
    if (selected.purpose === "internal") {
      if (!selected.canNetwork) return fail("can_network_required", snapshot({ ok: false }));
      if ((model || selected.model) !== "OBDLink MX+" || selected.canNetwork !== "drive") return fail("internal_profile_not_qualified", snapshot({ ok: false }));
      frameBatch = createCanFrameBatch();
    }
    const resolvedModel = model || selected.model;
    if (!MODELS.has(resolvedModel)) return fail("device_model_required");
    selected = { ...selected, deviceId: id, model: resolvedModel };
    persist();
    wantConnected = true;
    retryFails = 0;
    cancelRetry();
    if (!monitorChild) await startMonitor();
    return snapshot({ ok: true });
  }

  function reserveDispatch() {
    if (stopped) return false;
    if (pendingMutations || dispatchReserved || gate.owner() === "session") return false;
    dispatchReserved = true;
    return true;
  }

  function releaseDispatch() {
    dispatchReserved = false;
  }

  async function suspendForSession() {
    pausedForSession = true;
    cancelRetry();
    connected = false;
    clearReading();
    linkState = "diagnostic";
    const stoppedMon = await stopMonitorProcess();
    if (!stoppedMon.ok) {
      pausedForSession = false;
      return fail(stoppedMon.error || "monitor_close_timeout", snapshot({ ok: false }));
    }
    return { ok: true, ...FLAGS };
  }

  async function resumeAfterSession() {
    pausedForSession = false;
    releaseDispatch();
    if (stopped || !wantConnected) {
      if (!wantConnected) linkState = "idle";
      return;
    }
    await startMonitor();
  }

  function finishRecording(reason) {
    if (!recorder) return;
    const current = recorder;
    recorder = null;
    recordingResult = { active: false, file: current.file, frameCount: current.count, startedAt: current.startedAt,
      endedAt: new Date().toISOString(), reason };
    try { current.finish(reason, { events: recordingGaps, omitted: omittedRecordingGaps }); }
    catch (error) { recordingResult.error = String(error.message || error); }
  }

  async function dataAction(action) {
    if (action === "record-stop") { finishRecording("user-stopped"); return snapshot(); }
    if (selected.purpose !== "internal") return fail("physical_frames_unavailable", snapshot({ ok: false }));
    if (action === "new-batch") { frameBatch = createCanFrameBatch(); return snapshot(); }
    if (action === "save-result") {
      if (!frameBatch.view().retainedFrames) return fail("no_received_frames");
      if (!opts.saveResult) return fail("save_dialog_unavailable");
      const saved = await opts.saveResult({ ...frameBatch.export(), deviceId: selected.deviceId, canNetwork: selected.canNetwork });
      return snapshot({ saved });
    }
    if (!connected || !monitorChild) return fail("not_connected", snapshot({ ok: false }));
    if (recorder) return fail("already_recording", snapshot({ ok: false }));
    if (!opts.chooseRecordingFile) return fail("save_dialog_unavailable");
    const filePath = await opts.chooseRecordingFile();
    if (!filePath) return snapshot({ cancelled: true });
    if (!connected || !monitorChild || selected.purpose !== "internal") return fail("not_connected", snapshot({ ok: false }));
    try { recorder = (opts.createRecordingWriter || createCanPcapWriter)(filePath, selected.canNetwork); recordingResult = null; recordingGaps = []; omittedRecordingGaps = 0; }
    catch (error) { return fail(`recording_open_failed:${error.code || error.message}`, snapshot({ ok: false })); }
    return snapshot();
  }

  async function userStop(clearSelection) {
    wantConnected = false;
    finishRecording("user-disconnected");
    cancelRetry();
    connected = false;
    clearReading();
    linkState = "disconnecting";
    if (sessionBusy()) {
      if (!stopSession) return fail("busy", snapshot({ ok: false }));
      const ended = await stopSession();
      if (!ended.ok) {
        linkState = "close_failed";
        lastFatal = ended.error;
        return fail(ended.error, snapshot({ ok: false }));
      }
    }
    const result = await stopMonitorProcess();
    if (!result.ok) {
      linkState = "close_failed";
      lastFatal = result.error;
      return fail(result.error, snapshot({ ok: false }));
    }
    linkState = "idle";
    lastFatal = null;
    if (clearSelection) {
      selected = { ...selected, deviceId: null, model: null };
      persist();
    }
    return snapshot();
  }

  async function doSelect(request) {
    if (!request.deviceId) return fail("malformed_device_id");
    const hit = lastList.devices.find((d) => d.id === request.deviceId);
    if (!hit) return fail("unknown_device");
    if (!hit.available) {
      cancelRetry();
      wantConnected = false;
      connected = false;
      clearReading();
      const result = await stopMonitorProcess();
      if (!result.ok) return fail(result.error, snapshot({ ok: false }));
      selected = { ...selected, deviceId: request.deviceId, model: modelForSelection(request, hit) };
      persist();
      return fail("device_port_unavailable", snapshot({ ok: false }));
    }
    if (request.deviceId !== selected.deviceId) {
      const result = await userStop(false);
      if (!result.ok) return fail(result.error, snapshot({ ok: false }));
      frameBatch = createCanFrameBatch();
      recordingResult = null;
    }
    selected = { ...selected, deviceId: request.deviceId, model: modelForSelection(request, hit) };
    persist();
    return snapshot();
  }

  async function handle(request) {
    const bad = validateConnectionRequest(request);
    if (bad) return bad;
    if (stopped) return fail("connection_closed");
    if (request.action === "list") return listDevices();
    if (request.action === "status") return snapshot();
    if (request.action === "voltage") {
      if (!connected) return fail("not_connected", snapshot({ ok: false }));
      return snapshot();
    }
    if (request.action === "disconnect") return enqueue(() => userStop(false), true);
    if (sessionBusy()) return fail("busy");
    if (["record-start", "record-stop", "new-batch", "save-result"].includes(request.action)) return enqueue(() => dataAction(request.action));
    if (request.action === "configure") return enqueue(async () => {
      if (wantConnected || monitorChild || gate.owner()) return fail("disconnect_before_configure", snapshot({ ok: false }));
      selected = { ...selected, ...(request.purpose ? { purpose: request.purpose } : {}),
        ...(request.canNetwork ? { canNetwork: request.canNetwork } : {}) };
      frameBatch = createCanFrameBatch();
      persist();
      return snapshot();
    });
    if (request.action === "select") return enqueue(() => doSelect(request));
    if (request.action === "clear") return enqueue(() => userStop(true));
    const id = selected.deviceId;
    if (!id) return fail("device_not_selected");
    if (request.deviceId && request.deviceId !== id) return fail("device_mismatch");
    if (request.action === "connect") return enqueue(() => {
      if (request.deviceId && request.deviceId !== selected.deviceId) return fail("device_mismatch");
      return beginConnect(request.model);
    });
    return fail("invalid_action");
  }

  async function shutdown() {
    stopped = true;
    finishRecording("application-closed");
    wantConnected = false;
    cancelRetry();
    clearWatch();
    connected = false;
    clearReading();
    linkState = "idle";
    await stopMonitorProcess();
    ingestGen += 1;
  }

  return {
    handle,
    selectedId,
    voltageView,
    snapshot,
    noteLiveAtrv,
    shutdown,
    gate,
    reserveDispatch,
    releaseDispatch,
    suspendForSession,
    resumeAfterSession,
    setSessionStop: (fn) => { stopSession = fn; },
    _state: () => ({ selected, connected, reading, wantConnected, linkState, pausedForSession, dispatchReserved, monitorToken }),
  };
}

export function attachSessionHandoff(conn, sessionMgr, hooks = {}) {
  conn.setSessionStop(() => sessionMgr.cancelActiveAndWait());
  const orig = sessionMgr.handle.bind(sessionMgr);
  sessionMgr.handle = async (request, ctx) => {
    if (request?.action !== "prepare" && request?.action !== "start") return orig(request, ctx);
    const invalid = validateSessionRequest(request);
    if (invalid) return invalid;
    if (request.action === "start" && (request.mode || "simulation") === "live") {
      const blocked = liveBlocked({ spawnFn: hooks.spawnFn, allowInjectedLive: hooks.allowInjectedLive }, hooks.env || process.env);
      if (blocked) return fail("live_not_enabled", { reason: blocked });
      if (conn.snapshot().purpose !== "diagnostic") return fail("diagnostic_purpose_required");
      if (!conn.selectedId()) return fail("device_not_selected");
    }
    if (!conn.reserveDispatch()) return fail("busy");
    try {
      const sus = await conn.suspendForSession();
      if (sus && sus.ok === false) {
        conn.releaseDispatch();
        return sus;
      }
      const out = await orig(request, ctx);
      if (request.action === "prepare") await conn.resumeAfterSession();
      else if (!out?.ok && !out?.jobId) await conn.resumeAfterSession();
      return out;
    } catch (err) {
      await conn.resumeAfterSession();
      throw err;
    }
  };
  return sessionMgr;
}
