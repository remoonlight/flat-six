/** Actual installed Wireshark parser acceptance. Synthetic bytes only, no capture/device I/O. */
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { createCanPcapWriter } from "../apps/desktop/electron/internal-can-data.mjs";
import { createDiagnosticCanRecorder } from "../apps/desktop/electron/diagnostic-can-recording.mjs";

const root = path.resolve(import.meta.dirname, "..");
const args = process.argv.slice(2);
const options = {};
for (let n = 0; n < args.length; n += 2) {
  assert.ok(["--tshark", "--ui-fixtures"].includes(args[n]) && args[n + 1] && !(args[n] in options),
    "Usage: node scripts/pcapng-wireshark-accept.mjs [--tshark absolute-path] [--ui-fixtures directory]");
  options[args[n]] = args[n + 1];
}
const tshark = options["--tshark"] || (process.platform === "win32"
  ? path.join(process.env.ProgramFiles || "C:\\Program Files", "Wireshark", "tshark.exe") : "tshark");
const capinfos = process.platform === "win32" ? path.join(path.dirname(tshark), "capinfos.exe") : "capinfos";
const base = path.join(root, ".local/obd-pcapng-wireshark-20261005");
fs.mkdirSync(base, { recursive: true });
const scratch = fs.mkdtempSync(path.join(base, "run-"));
const report = { ok: false, scratch, createdUtc: new Date().toISOString(), noHardwareIO: true, vehicleValidated: false, checks: [], captures: [] };
const fields = ["frame.number", "frame.time_epoch", "can.id", "can.flags.xtd", "can.len", "data.data",
  "frame.interface_name", "frame.comment", "_ws.malformed", "can.flags.rtr", "can.flags.err"];
// Raw CAN acceptance does not qualify guessed vehicle protocols. On empty
// frames data returns zero and Wireshark otherwise falls back to heuristics.
const rawOptions = ["-d", "can.subdissector,data", ...[
  "autosar_nm_can_heur", "xcp_can_heur", "signal_pdu_can_heur", "obd-ii_can_heur", "ipdu_multiplexer_can_heur",
].flatMap((name) => ["--disable-heuristic", name])];
const write = (name, text) => fs.writeFileSync(path.join(scratch, name), text);
const hash = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
function run(exe, argv, log) {
  try {
    const result = execFileSync(exe, argv, { encoding: "utf8", timeout: 15000, maxBuffer: 4 * 1024 * 1024, windowsHide: true });
    write(log, result); return result;
  } catch (error) {
    write(log, String(error.stdout || "") + String(error.stderr || "")); throw error;
  }
}
function read(file, label) {
  const content = run(tshark, ["-n", "-r", file, ...rawOptions, "-T", "fields",
    ...fields.flatMap((field) => ["-e", field])], `${label}-frames.tsv`);
  const info = run(capinfos, [file], `${label}-capinfos.log`);
  assert.match(info, /pcapng/); assert.match(info, /SocketCAN/);
  assert.match(info, /microseconds \(6\)/); assert.match(info, /Capture length = 16/);
  assert.match(info, /Time ticks per second = 1000000/);
  assert.match(info, /Number of stat entries = 1/);
  const frames = content.trimEnd().split(/\r?\n/).filter(Boolean).map((line) => {
    const [number, epoch, id, extended, dlc, data, network, comment, malformed, rtr, err] = line.split("\t");
    assert.equal(malformed, "", "Wireshark must not report malformed packets");
    assert.match(rtr, /^(?:0|False)$/i); assert.match(err, /^(?:0|False)$/i);
    const match = /^(\d+)\.(\d{1,9})$/.exec(epoch);
    assert.ok(match, "TShark fields must expose numeric epoch time");
    assert.equal(match[2].padEnd(9, "0").slice(6), "000", "only microsecond precision is claimed");
    assert.match(extended, /^(?:0|1|False|True)$/i);
    return { number: Number(number), timestampUs: Number(BigInt(match[1]) * 1000000n + BigInt(match[2].padEnd(6, "0").slice(0, 6))),
      canId: Number(id), extended: /^(?:1|True)$/i.test(extended), dataHex: data.toUpperCase(), dlc: Number(dlc), network, comment };
  });
  assert.equal(Number(/Number of packets:\s+(\d+)/.exec(info)?.[1]), frames.length);
  assert.ok(frames.length > 0);
  for (const frame of frames) assert.equal(frame.dlc, frame.dataHex.length / 2);
  report.captures.push({ file, sha256: hash(file), frames: frames.length, encapsulation: "SocketCAN", timestampPrecision: "microseconds" });
  return { frames, info };
}
function compare(actual, expected) {
  assert.equal(actual.length, expected.length);
  for (let n = 0; n < expected.length; n++) {
    const { canId, extended, timestampUs, dataHex } = expected[n];
    assert.equal(actual[n].number, n + 1);
    assert.deepEqual({ canId: actual[n].canId, extended: actual[n].extended, timestampUs: actual[n].timestampUs,
      dataHex: actual[n].dataHex }, { canId, extended, timestampUs, dataHex });
  }
}
try {
  report.sourceHashes = Object.fromEntries([
    "scripts/pcapng-wireshark-accept.mjs", "apps/desktop/electron/internal-can-data.mjs",
    "apps/desktop/electron/diagnostic-can-recording.mjs",
  ].map((name) => [name, hash(path.join(root, name))]));
  const version = run(tshark, ["--version"], "version.log").split(/\r?\n/)[0];
  assert.match(version, /^TShark \(Wireshark\)/); report.version = version;
  const stamp = Date.now() * 1000;
  const frames = [
    [0, false, ""], [0x123, false, "00"], [0x7ff, false, "00112233445566FF"],
    [0x18daf101, true, "AABBCC"], [0x1fffffff, true, "FFEE010203040506"],
    [0x123, true, "DEAD"], [0, false, "FFEEDDCCBBAA9988"],
  ].map(([canId, extended, dataHex], n) => ({ canId, extended, dataHex,
    timestampUs: stamp + n * 1000 + n + 123, timestampSource: "host-chunk-arrival" }));
  const internal = path.join(scratch, "SYNTHETIC-can-boundaries.pcapng");
  const writer = createCanPcapWriter(internal, "SYNTHETIC Drive fixture", {
    description: "SYNTHETIC no-car fixture. Host microseconds, no physical bus timing or hardware evidence." });
  writer.append(frames); writer.finish("synthetic-validation", [{ reason: "synthetic-gap-only" }]);
  const decoded = read(internal, "internal"); compare(decoded.frames, frames);
  assert.match(decoded.info, /SYNTHETIC/);
  assert.ok(decoded.frames.every((frame) => frame.network.startsWith("SYNTHETIC Drive")));
  report.checks.push("standard/extended IDs including maximum and same ID with different IDE", "DLC 0/1/2/3/8 and exact bytes",
    "big-endian SocketCAN header", "exact microsecond timestamps", "interface and statistics block", "synthetic source comment",
    "raw CAN decoding with unqualified application heuristics disabled");

  const diagnostic = path.join(scratch, "SYNTHETIC-explicit-diagnostic.pcapng");
  const recorder = createDiagnosticCanRecorder({ chooseFile: async () => diagnostic });
  const event = (frame) => ({ frame, simulation: true, profileId: "porsche-981-2014-dme", runId: "s-synthetic-parser-test" });
  recorder.onFrame(event(frames[1])); // Before explicit start, no file/frame.
  assert.equal(fs.existsSync(diagnostic), false);
  assert.equal((await recorder.handle({ action: "start", simulation: true })).ok, true);
  const diagnosticStart = Date.now() * 1000 + 1000;
  const received = frames.slice(1, 3).map((frame, n) => ({ ...frame, timestampUs: diagnosticStart + n * 1234 }));
  recorder.onFrame(event({ ...frames[1], timestampUs: 1 })); // Stale pre-start frame is discarded.
  for (const frame of received) recorder.onFrame(event(frame));
  await recorder.handle({ action: "stop" });
  recorder.onFrame(event(frames[1])); // After stop, no additional packet.
  const diag = read(diagnostic, "diagnostic"); compare(diag.frames, received);
  assert.match(diag.info, /SYNTHETIC/);
  for (const frame of diag.frames) assert.deepEqual(JSON.parse(frame.comment), {
    profileId: "porsche-981-2014-dme", runId: "s-synthetic-parser-test", simulation: true });
  report.checks.push("explicit recording interval excludes before/start-stale/after frames", "per-packet profile/run/simulation provenance");

  if (options["--ui-fixtures"]) {
    const directory = path.resolve(options["--ui-fixtures"]);
    const batch = JSON.parse(fs.readFileSync(path.join(directory, "explicit-result.json"), "utf8"));
    assert.equal(batch.kind, "internal-can-acquisition");
    assert.equal(batch.physicalSilenceVerified, false, "Injected UI fixtures are not physical bus proof");
    const oldInternal = read(path.join(directory, "explicit.pcapng"), "previous-ui-internal");
    for (const frame of oldInternal.frames) {
      assert.ok(batch.frames.some((s) => s.canId === frame.canId && s.extended === frame.extended
        && s.dataHex === frame.dataHex && s.timestampUs === frame.timestampUs), "recorded UI frame must match saved source batch");
    }
    const oldDiagnostic = read(path.join(directory, "diagnostic-explicit.pcapng"), "previous-ui-diagnostic");
    assert.match(oldDiagnostic.info, /SYNTHETIC/);
    const runs = new Set();
    for (const frame of oldDiagnostic.frames) {
      const comment = JSON.parse(frame.comment);
      assert.equal(comment.simulation, true); assert.equal(comment.profileId, "porsche-981-2014-dme");
      assert.ok(typeof comment.runId === "string" && comment.runId.length > 8);
      assert.equal(frame.canId, 0x7e8); assert.equal(frame.extended, false);
      runs.add(comment.runId);
    }
    assert.ok(runs.size > 1, "cross-session recording retains distinct run IDs");
    report.previousUI = { injectedOnly: true, hardwareIO: false, diagnosticRuns: runs.size,
      internalSourceFrames: batch.frames.length, recordedInternalFrames: oldInternal.frames.length };
    report.checks.push("earlier Electron internal capture matches actual saved fixture frames", "earlier multi-session diagnostic capture keeps simulation/run IDs");
  }

  const truncated = path.join(scratch, "SYNTHETIC-truncated.pcapng");
  const complete = fs.readFileSync(internal);
  let offset = 0;
  while (complete.readUInt32LE(offset) !== 6) offset += complete.readUInt32LE(offset + 4);
  // Truncate a recognized packet body, rather than an incomplete initial
  // header which some readers treat as an empty capture with exit status 0.
  fs.writeFileSync(truncated, complete.subarray(0, offset + 8 + 20 + 5));
  const damaged = spawnSync(tshark, ["-n", "-r", truncated, "-T", "fields", "-e", "frame.number"], {
    encoding: "utf8", timeout: 15000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  write("truncated.log", String(damaged.stdout || "") + String(damaged.stderr || ""));
  assert.equal(damaged.error, undefined);
  assert.ok(Number.isInteger(damaged.status) && damaged.status !== 0, "TShark must reject a truncated packet");
  report.checks.push("truncated packet rejected");
  report.ok = true;
  console.log(`PASS actual ${version}: ${report.captures.length} synthetic/injected captures, ${report.captures.reduce((count, capture) => count + capture.frames, 0)} frames, exact CAN fields/time/provenance, truncated packet rejected`);
} catch (error) {
  report.error = String(error.stack || error); throw error;
} finally {
  fs.writeFileSync(path.join(scratch, "result.json"), JSON.stringify(report, null, 2) + "\n");
  fs.writeFileSync(path.join(base, "latest.json"), JSON.stringify({ ...report, result: path.join(scratch, "result.json") }, null, 2) + "\n");
}
