/**
 * Self-check: validate + mock spawn + real workbench roundtrip.
 * Run: node apps/desktop/electron/offline-diagnostics.selfcheck.mjs
 */
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { fail, runWorkbench, validateRequest, createOfflineOperations } from "./offline-diagnostics.mjs";
import { createScopeTokens } from "./offline-scope-guard.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function mockChild({
  stdout = '{"ok":true}',
  stderr = "",
  code = 0,
  hang = false,
  flood = 0,
  emitCloseOnKill = true,
  extraAfterCap = false,
} = {}) {
  const child = new EventEmitter();
  child.killed = false;
  child.exitCode = null;
  child.stdin = new EventEmitter();
  child.stdin.write = () => true;
  child.stdin.end = () => {};
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {
    child.killed = true;
    child.exitCode = 1;
    if (emitCloseOnKill) queueMicrotask(() => child.emit("close", 1));
  };
  queueMicrotask(() => {
    if (hang) return;
    if (flood) {
      child.stdout.emit("data", Buffer.alloc(flood, 0x41));
      if (extraAfterCap) child.stdout.emit("data", Buffer.alloc(32, 0x42));
      return;
    }
    child.stdout.emit("data", Buffer.from(stdout));
    if (stderr) child.stderr.emit("data", Buffer.from(stderr));
    child.exitCode = code;
    child.emit("close", code);
  });
  return child;
}

const bad = validateRequest({ action: "explode" });
assert(validateRequest({ action: "ready-plan", parameterIds: ["../path"] }).error === "invalid_parameter_selection", "ready path rejected");
assert(validateRequest({ action: "ready-parameters", groupId: "../../db" }).error === "invalid_group_id", "ready group path rejected");
assert(validateRequest({ action: "ready-plan", parameterIds: Array(13).fill("a".repeat(64)) }).error === "invalid_parameter_selection", "ready cap");
assert(validateRequest({ action: "ready-plan", parameterIds: ["a".repeat(64), "a".repeat(64)] }).error === "invalid_parameter_selection", "ready duplicate");
assert(validateRequest({ action: "ready-parameters", ecuId: 2, profileId: "test", limit: 40 }) === null, "ready listing");
assert(validateRequest({ action: "coding-options", expectedReadRequestHex: "2110" }) === null, "LID coding block");
assert(validateRequest({ action: "coding-options", expectedReadRequestHex: "220010" }) === null, "DID coding block");
for (const hx of ["210010", "2210", "2210XX", 2110])
  assert(validateRequest({ action: "coding-options", expectedReadRequestHex: hx }).error === "coding_block_request_invalid", "invalid block request rejected before spawn");
assert(bad && bad.error === "invalid_action", "invalid_action");
assert(validateRequest({ action: "summary", send: 1 }).error === "forbidden_field", "send");
assert(validateRequest({ action: "summary", formula: "x" }).error === "forbidden_field", "formula");
assert(validateRequest({ action: "plan", generation: "991" }).error === "wrong_generation", "gen");
assert(validateRequest({ action: "variants", generation: "981", limit: 101 }).error === "cap_limit", "limit");
assert(fail("x").executionEnabled === false && fail("x").writePayload === null, "flags");

const scope = createScopeTokens();
const decodeTok = scope.next("decode");
const planTok = scope.next("plan");
let lateDecode = null;
let latePlan = null;
const delayed = new Promise((resolve) => {
  setTimeout(() => {
    if (scope.live("decode", decodeTok)) lateDecode = { text: "是" };
    if (scope.live("plan", planTok)) latePlan = { groupCount: 35 };
    resolve();
  }, 30);
});
scope.invalidate(["decode", "plan", "match"]);
await delayed;
assert(lateDecode === null && latePlan === null, "stale decode/plan dropped");
assert(!scope.live("decode", decodeTok), "decode token advanced");
assert(scope.live("summary", scope.next("summary")), "summary independent");

const mocked = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    spawnFn: () => mockChild({ stdout: JSON.stringify({ ok: true, menuEcuCount: 35 }) }),
  },
);
assert(mocked.ok === true && mocked.menuEcuCount === 35, "mock ok");
assert(mocked.executionEnabled === false, "force flags");

const timed = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    timeoutMs: 20,
    spawnFn: () => mockChild({ hang: true }),
  },
);
assert(timed.error === "timeout", `timeout got ${timed.error}`);

let cancelledChild;
const operations = createOfflineOperations({ repoRoot, spawnFn: () => (cancelledChild = mockChild({ hang: true })) });
const preparation = operations.run({ action: "ready-plan" }, 10, "preparation-1");
assert(operations.cancel(11, "preparation-1").cancelled === false, "different window cannot cancel");
assert(cancelledChild.killed === false, "foreign stop leaves job running");
assert((await operations.run({ action: "ready-plan" }, 10, "preparation-1")).error === "operation_exists", "duplicate operation rejected");
operations.cancel(10, "preparation-1");
assert((await preparation).error === "cancelled" && cancelledChild.killed, "stop kills offline worker");
assert(operations.cancel(10, "preparation-1").cancelled === false, "finished operation removed");
const closing = operations.run({ action: "ready-plan" }, 10, "preparation-2");
operations.cancelOwned(10);
assert((await closing).error === "cancelled", "window close cancels worker");
for (const action of ["ready-replay", "catalog-plan", "catalog-replay"]) {
  const pending = operations.run({ action }, 10, action);
  assert(operations.cancel(11, action).cancelled === false, "foreign window cannot stop replay/catalogue work");
  assert(operations.cancel(10, action).cancelled === true, `${action} accepts a cancellable window-owned operation`);
  assert((await pending).error === "cancelled", `${action} cancellation reaches worker`);
}
const aborted = new AbortController(); aborted.abort();
assert((await runWorkbench({ action: "summary" }, { repoRoot, signal: aborted.signal,
  spawnFn: () => { throw new Error("must not spawn"); } })).error === "cancelled", "abort before spawn");

const cap = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    maxStdout: 16,
    spawnFn: () => mockChild({ flood: 64 }),
  },
);
assert(cap.error === "output_cap", `cap got ${cap.error}`);

const nz = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    spawnFn: () =>
      mockChild({ stdout: JSON.stringify({ ok: false, error: "invalid_action" }), code: 2 }),
  },
);
assert(nz.ok === false && nz.error === "invalid_action", "nonzero envelope");

let hanging;
const cleaned = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    timeoutMs: 20,
    spawnFn: () => {
      hanging = mockChild({ hang: true });
      return hanging;
    },
  },
);
assert(cleaned.error === "timeout" && hanging.killed === true, "cleanup kill");

let n = 0;
const fallback = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    spawnFn: () => {
      n += 1;
      if (n === 1) {
        const c = mockChild({ hang: true });
        queueMicrotask(() => {
          const err = new Error("enoent");
          err.code = "ENOENT";
          c.emit("error", err);
          c.emit("close", 1);
        });
        return c;
      }
      return mockChild({ stdout: JSON.stringify({ ok: true, menuEcuCount: 35 }) });
    },
  },
);
assert(fallback.ok === true && fallback.menuEcuCount === 35, `fallback ${JSON.stringify(fallback)}`);

const noClose = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    timeoutMs: 20,
    spawnFn: () => mockChild({ hang: true, emitCloseOnKill: false }),
  },
);
assert(noClose.error === "timeout", `no-close timeout ${noClose.error}`);

const capExtra = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    maxStdout: 16,
    spawnFn: () => mockChild({ flood: 64, extraAfterCap: true }),
  },
);
assert(capExtra.error === "output_cap", `cap extra ${capExtra.error}`);

const pipe = await runWorkbench(
  { action: "summary" },
  {
    repoRoot,
    spawnFn: () => {
      const c = mockChild({ hang: true });
      const orig = c.stdin.write;
      c.stdin.write = (...args) => {
        queueMicrotask(() => {
          const err = new Error("pipe");
          err.code = "EPIPE";
          c.stdin.emit("error", err);
        });
        return orig.apply(c.stdin, args);
      };
      return c;
    },
  },
);
assert(pipe.error === "stdin_closed", `epipe ${pipe.error}`);

const real = await runWorkbench(
  { action: "summary" },
  { repoRoot, timeoutMs: 60_000 },
);
assert(real.ok === true, `real summary ${JSON.stringify(real).slice(0, 200)}`);
assert(real.menuEcuCount === 35, "35 ecus");
assert(real.executionEnabled === false, "real flags");
assert(real.writePayload === null, "no write");

const td = fs.mkdtempSync(path.join(repoRoot, ".local", "offline-wb-"));
const fixture = path.join(td, "variants.jsonl");
const eu5 = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
fs.writeFileSync(
  fixture,
  JSON.stringify({
    profile_id: eu5,
    name: "SDI9_1_981_3_4L_EU5",
    module: "DME_BDE_Continental",
    generation: "981",
    membership: "confirmed",
    on_target_menu: true,
    pool_records: {
      coding: {
        count: 1,
        records: [
          {
            at: 4047899,
            byteOffset: 0,
            bitOffset: 0,
            formula: {
              text: "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;",
            },
            enumText: { F0000110: "是", F000010F: "否" },
            labels: [{ text: "巡航控制" }],
          },
        ],
      },
    },
  }) + "\n",
);
const dec = await runWorkbench(
  { action: "decode", generation: "981", profileId: eu5, category: "coding", recordAt: 4047899, dataHex: "01" },
  { repoRoot, variantsPath: fixture, timeoutMs: 30_000 },
);
assert(
  dec.ok === true && (dec.text === "是" || dec.textId === "F0000110"),
  `decode ${JSON.stringify(dec)}`,
);
const prev = await runWorkbench(
  {
    action: "preview",
    generation: "981",
    profileId: eu5,
    category: "coding",
    recordAt: 4047899,
    dataHex: "A5",
    rawValue: 0,
  },
  { repoRoot, variantsPath: fixture, timeoutMs: 30_000 },
);
assert(prev.ok === true && prev.afterHex === "A4", `preview ${JSON.stringify(prev)}`);
fs.rmSync(td, { recursive: true, force: true });

console.log("offline-diagnostics.selfcheck: ok");
