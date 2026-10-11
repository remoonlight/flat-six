// Actual host runtime clock comparison. No device/vehicle IO or hardware claim.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { CAPTURE_CLOCK, captureNowUs } from "./capture-clock.mjs";
const python = process.env.PORSCHE981_PYTHON || "python3";
const start = captureNowUs();
const run = spawnSync(python, ["-B", "-c", "import json,sys;from scripts.diagnostics.capture_clock import capture_timestamp;print(json.dumps({**capture_timestamp(),'python':sys.version.split()[0]}))"],
  {cwd:process.cwd(),encoding:"utf8",timeout:10000,windowsHide:true});
const end = captureNowUs();
assert.equal(run.status,0,run.stderr);
const result = JSON.parse(run.stdout);
assert.equal(result.monotonicClock,CAPTURE_CLOCK);
assert.ok(result.monotonicTimestampUs >= start-1 && result.monotonicTimestampUs <= end+1,
  "Python observation clock must lie inside the actual Node process bracket");
console.log(JSON.stringify({ok:true,host:process.platform,node:process.versions.node,uv:process.versions.uv,
  electron:process.versions.electron || null,python:result.python,clock:CAPTURE_CLOCK,
  bracketUs:[start,end],observedUs:result.monotonicTimestampUs,deviceIO:false,vehicleVerified:false}));
