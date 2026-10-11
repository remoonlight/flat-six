import { parentPort } from "node:worker_threads";
import { readFile } from "node:fs/promises";
import { prepareReplay, replayView } from "./acquisition-replay.mjs";

// Parsing/indexing stays off Electron's device/deadline event loop. Only bounded
// views cross back to main. Native-selected paths originate exclusively in main.
let record;
parentPort.on("message", async ({ id, request }) => {
  let result;
  try {
    if (request.action === "load") {
      const json = request.text ?? await readFile(request.fileName, "utf8");
      record = prepareReplay(JSON.parse(json), request.fileName);
      result = { ok: true, summary: record.summary };
    } else result = record ? replayView(record, request) : { ok: false, error: "replay_not_found" };
  } catch (error) {
    result = { ok: false, error: error instanceof SyntaxError ? "replay_invalid_json" : error?.code || error?.message || "replay_open_failed" };
  }
  parentPort.postMessage({ id, result });
});
