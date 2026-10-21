// Independent PCAPNG reader + real recorder; synthetic bytes, no device IO.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createDiagnosticPduWriter, validDiagnosticPdu } from "./diagnostic-pdu-data.mjs";
import { createDiagnosticCanRecorder } from "./diagnostic-can-recording.mjs";
import { CAPTURE_CLOCK, validCaptureClock } from "./capture-clock.mjs";

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-pdu-"));
const pdu = { requestHex: "22F100", pduHex: "7F2231", timestampUs: Date.now()*1000+1000,
  timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp", observation: "native-result-data",
  duringCleanup: false, primitive: 88, expectedPrimitive: 1, uniqueId: 77, flagsHex: "FF000000" };
function read(file, layer = "application") {
  const data = fs.readFileSync(file), packets = []; let pos = 0, stats;
  while (pos < data.length) {
    const type = data.readUInt32LE(pos), size = data.readUInt32LE(pos+4);
    assert.ok(size >= 12 && size % 4 === 0 && pos+size <= data.length);
    assert.equal(data.readUInt32LE(pos+size-4), size);
    const body = data.subarray(pos+8,pos+size-4);
    if (type === 1) { assert.equal(body.readUInt16LE(),layer === "application" ? 252 : 227); }
    if (type === 6) {
      const length = body.readUInt32LE(12); assert.equal(body.readUInt32LE(16),length);
      const packet = body.subarray(20,20+length);
      if (layer === "application") {
        assert.equal(packet.readUInt16BE(),12); assert.equal(packet.readUInt16BE(2),4);
        assert.equal(packet.subarray(4,8).toString(),"data"); assert.equal(packet.readUInt32BE(8),0);
      } else { assert.equal(packet.readUInt32BE(),0x7e8); assert.equal(packet[4],2); }
      const optionStart = 20+Math.ceil(length/4)*4;
      assert.equal(body.readUInt16LE(optionStart),1);
      const metadata = JSON.parse(body.subarray(optionStart+4,optionStart+4+body.readUInt16LE(optionStart+2)).toString());
      packets.push({ hex: (layer === "application" ? packet.subarray(12) : packet.subarray(8,10)).toString("hex").toUpperCase(), metadata,
        stamp: Number(BigInt(body.readUInt32LE(4))*0x100000000n+BigInt(body.readUInt32LE(8))) });
    }
    if (type === 5) { assert.equal(body.readUInt16LE(12),4); stats = Number(body.readBigUInt64LE(16)); }
    pos += size;
  }
  assert.equal(pos,data.length); assert.equal(stats,packets.length); return packets;
}
try {
  const mode50 = {...pdu, transportKind:"d-pdu-mode50", observation:"reassembled-complete-pdu",
    primitive:19, expectedPrimitive:19, uniqueId:1, flagsHex:"00000000"};
  assert.equal(validDiagnosticPdu(mode50),true);
  for (const extra of [{observation:"native-result-data"},{primitive:true},{uniqueId:true},
    {expectedPrimitive:true},{flagsHex:"not-hex"}]) assert.equal(validDiagnosticPdu({...mode50,...extra}),false);
  assert.equal(validDiagnosticPdu({...mode50,transportKind:"d-pdu-iso-tp"}),false);
  const mode50File=path.join(scratch,"mode50-reassembled.pcapng");
  const mode50Writer=createDiagnosticPduWriter(mode50File,true);
  mode50Writer.append(mode50,{simulation:true,runId:"owned-raw-model"});mode50Writer.finish("software-test");
  const [mode50Packet]=read(mode50File);
  assert.equal(mode50Packet.hex,mode50.pduHex);
  assert.equal(mode50Packet.metadata.transportKind,"d-pdu-mode50");
  assert.equal(mode50Packet.metadata.observation,"reassembled-complete-pdu");
  assert.equal(mode50Packet.metadata.primitive,mode50Packet.metadata.expectedPrimitive);
  for (const transportKind of ["elm-mode30","d-pdu-mode30"]) {
    const row={...mode50,transportKind};
    if(transportKind==="elm-mode30")for(const key of ["primitive","expectedPrimitive","uniqueId","flagsHex"])delete row[key];
    assert.equal(validDiagnosticPdu(row),true);
    assert.equal(validDiagnosticPdu({...row,observation:"native-result-data"}),false);
    const target=path.join(scratch,transportKind+".pcapng"),out=createDiagnosticPduWriter(target,true);
    out.append(row,{simulation:true,runId:"tp20-raw-model"});out.finish("software-test");
    const [packet]=read(target);assert.equal(packet.hex,row.pduHex);
    assert.equal(packet.metadata.transportKind,transportKind);
  }
  const file = path.join(scratch,"short-writes.pcapng");
  const writer = createDiagnosticPduWriter(file,true,{ write: (fd,b,offset,length) => fs.writeSync(fd,b,offset,Math.min(length,7)) });
  assert.throws(() => createDiagnosticPduWriter(file,true), /EEXIST/);
  for (const hex of ["7F2231","62","62F1002A07"]) writer.append({...pdu,pduHex:hex},{simulation:true,targetProfileId:"requested-only",runId:"synthetic"});
  writer.finish("software-test"); writer.finish("idempotent");
  const packets = read(file); assert.deepEqual(packets.map(p=>p.hex),["7F2231","62","62F1002A07"]);
  assert.ok(packets.every(p=>p.stamp === pdu.timestampUs && p.metadata.primitive === 88 && p.metadata.targetProfileId === "requested-only"));
  assert.ok(packets.every(p=>p.metadata.expectedPrimitive === 1));
  assert.equal(validDiagnosticPdu({...pdu,expectedPrimitive:true}),false);
  assert.equal(validDiagnosticPdu({...pdu,expectedPrimitive:-1}),false);
  assert.equal(validDiagnosticPdu({...pdu,expectedPrimitive:0xffffffff+1}),false);
  assert.match(fs.readFileSync(file).toString(), /SYNTHETIC; not vehicle evidence/);
  let fdClosed = false;
  assert.throws(()=>createDiagnosticPduWriter(path.join(scratch,"failed.pcapng"),true,{
    write:()=>0,close:fd=>{fdClosed=true;fs.closeSync(fd);}
  }),/pcap-short-write/); assert.equal(fdClosed,true);

  for (const partial of [false, true]) {
    const failedFile = path.join(scratch, `lost-write-${partial}.pcapng`);
    let fail = false, wrotePartial = false, closed = false;
    const interrupted = createDiagnosticPduWriter(failedFile, true, {
      write: (fd, bytes, offset, length) => {
        if (fail) {
          if (partial && !wrotePartial) { wrotePartial = true; return fs.writeSync(fd, bytes, offset, Math.min(5, length)); }
          fail = false; throw Object.assign(new Error("disk write failed"), {code:"ENOSPC"});
        }
        return fs.writeSync(fd, bytes, offset, length);
      }, close: fd => { fs.closeSync(fd); closed = true; },
    });
    interrupted.append(pdu, {simulation:true,targetProfileId:"requested",runId:"disk-fault"});
    const prefix = fs.readFileSync(failedFile); fail = true;
    assert.throws(() => interrupted.append(pdu, {}), /disk write failed/);
    const failedBytes = fs.readFileSync(failedFile);
    assert.throws(() => interrupted.append(pdu, {}), /pcap-write-failed/);
    assert.deepEqual(fs.readFileSync(failedFile), failedBytes, "a recovered disk cannot resume the failed file");
    interrupted.finish("recording-write-failed"); interrupted.finish("idempotent");
    assert.equal(closed, true); assert.equal(interrupted.count, 1);
    assert.deepEqual(fs.readFileSync(failedFile), failedBytes, "no normal end statistics after a write failure");
    assert.deepEqual(failedBytes.subarray(0,prefix.length), prefix, "complete received prefix survives");
    assert.throws(() => read(failedFile), undefined, "independent reader cannot accept it as a complete recording");
  }

  const recorded = path.join(scratch,"explicit.pcapng"); let readyLayer, choices=0;
  const recorder = createDiagnosticCanRecorder({ chooseFile:async()=>{choices++;return recorded;},isLiveReady:layer=>{readyLayer=layer;return true;} });
  const event = pdu => ({pdu,simulation:false,profileId:"requested",runId:"software-injected"});
  recorder.onPdu(event(pdu)); assert.equal(fs.existsSync(recorded),false);
  assert.equal((await recorder.handle({action:"start",simulation:false,layer:"unknown"})).ok,false); assert.equal(choices,0);
  assert.equal((await recorder.handle({action:"start",simulation:false,layer:"application"})).ok,true); assert.equal(readyLayer,"application");
  recorder.onPdu(event({...pdu,timestampUs:1})); assert.equal(recorder.snapshot().recording.frameCount,0);
  recorder.onPdu(event({...pdu,startedTimestampUs:1,timestampUs:Date.now()*1000+1000}));
  assert.equal(recorder.snapshot().recording.frameCount,0,"a response begun before recording is excluded even when completed inside it");
  recorder.onPdu(event({...pdu,timestampUs:Date.now()*1000+1000}));
  await recorder.handle({action:"stop"}); recorder.onPdu(event(pdu));
  assert.equal(read(recorded).length,1);
  const invalidTime = createDiagnosticCanRecorder({chooseFile:async()=>path.join(scratch,"invalid-time.pcapng")});
  await invalidTime.handle({action:"start",simulation:true,layer:"application"});
  invalidTime.onPdu({pdu:{...pdu,startedTimestampUs:pdu.timestampUs+1},simulation:true});
  assert.equal(invalidTime.snapshot().recording.reason,"invalid-pdu");invalidTime.shutdown();
  const switched = path.join(scratch,"switched.pcapng");
  const other = createDiagnosticCanRecorder({chooseFile:async()=>switched});
  await other.handle({action:"start",simulation:true,layer:"application"}); other.onPdu(event(pdu));
  assert.equal(other.snapshot().recording.reason,"source-changed"); assert.equal(read(switched).length,0);
  other.shutdown(); recorder.shutdown();
  // Delayed event-loop callbacks, idle expiry and stale timers must not extend
  // a recording or terminate the next explicit batch. No ten-minute sleep.
  for (const layer of ["can", "application"]) {
    let clock = Date.now(), chosen = 0, nextTimer = 0;
    const timers = new Map(), cleared = new Set();
    const timed = createDiagnosticCanRecorder({
      chooseFile: async () => path.join(scratch, `deadline-${layer}-${++chosen}.pcapng`),
      nowFn: () => clock, elapsedNowFn: () => clock,
      setTimeoutFn: (fn, delay) => { assert.equal(delay,600000); timers.set(++nextTimer,fn); return nextTimer; },
      clearTimeoutFn: id => cleared.add(id),
    });
    const receive = () => layer === "application"
      ? timed.onPdu({simulation:true,pdu:{...pdu,timestampUs:clock*1000}})
      : timed.onFrame({simulation:true,frame:{canId:0x7e8,extended:false,dataHex:"0102",timestampUs:clock*1000,timestampSource:"host-chunk-arrival"}});
    const opening = await timed.handle({action:"start",simulation:true,layer});
    assert.equal(opening.recording.active,true);
    clock += 599999; receive();
    assert.equal(timed.snapshot().recording.frameCount,1);
    clock++; receive(); // The actual timeout callback has not run.
    const ended = timed.snapshot().recording;
    assert.equal(ended.active,false); assert.equal(ended.reason,"time-limit");
    assert.equal(ended.frameCount,1); assert.ok(cleared.has(1));
    assert.equal(Date.parse(ended.endedAt)-Date.parse(ended.startedAt),600000);
    const completed = fs.readFileSync(ended.file);
    assert.ok(completed.includes(Buffer.from('"reason":"time-limit"')));
    if (layer === "application") assert.equal(read(ended.file).length,1);
    receive(); assert.deepEqual(fs.readFileSync(ended.file),completed);
    // A fresh explicit batch gets a fresh interval; a queued old callback
    // cannot close the new file even if clearing that callback was too late.
    clock += 1000;
    const restarted = await timed.handle({action:"start",simulation:true,layer});
    timers.get(1)(); assert.equal(timed.snapshot().recording.active,true);
    assert.equal(timed.snapshot().recording.file,restarted.recording.file);
    clock += 600000; timers.get(2)(); // No data/status polling needed to close.
    assert.ok(fs.readFileSync(restarted.recording.file).includes(Buffer.from('"reason":"time-limit"')));
    assert.equal(timed.snapshot().recording.reason,"time-limit");
    assert.equal(timed.snapshot().recording.frameCount,0);
    if (layer === "application") assert.equal(read(restarted.recording.file).length,0);
    timed.shutdown();
  }
  for (const layer of ["can", "application"]) {
    let wall = Date.now(), elapsed = 0;
    const corrected = createDiagnosticCanRecorder({
      chooseFile: async () => path.join(scratch, `clock-correction-${layer}.pcapng`),
      nowFn: () => wall, elapsedNowFn: () => elapsed,
      captureNowUsFn: () => 1000000 + elapsed * 1000,
      setTimeoutFn: () => 1, clearTimeoutFn: () => {},
    });
    await corrected.handle({action:"start",simulation:true,layer});
    elapsed = 1000; wall += 3600000;
    assert.equal(corrected.snapshot().recording.active,true,"forward wall-clock correction cannot end a recording early");
    const receivedAt = wall * 1000;
    if (layer === "application") corrected.onPdu({simulation:true,pdu:{...pdu,timestampUs:receivedAt}});
    else corrected.onFrame({simulation:true,frame:{canId:0x7e8,extended:false,dataHex:"0102",timestampUs:receivedAt,timestampSource:"host-chunk-arrival"}});
    assert.equal(corrected.snapshot().recording.frameCount,1);
    elapsed = 2000; wall -= 7200000;
    assert.equal(corrected.snapshot().recording.active,true,"backward wall-clock correction cannot alter the elapsed deadline");
    const observation = {monotonicClock:CAPTURE_CLOCK,monotonicTimestampUs:3000000};
    if (layer === "application") corrected.onPdu({simulation:true,pdu:{...pdu,...observation,timestampUs:wall*1000}});
    else corrected.onFrame({simulation:true,frame:{canId:0x7e8,extended:false,dataHex:"0304",timestampUs:wall*1000,timestampSource:"host-chunk-arrival",...observation}});
    assert.equal(corrected.snapshot().recording.frameCount,2,"newly received bytes remain recorded after wall-clock rollback");
    elapsed = 599999;
    assert.equal(corrected.snapshot().recording.active,true);
    elapsed = 600000;
    const ended = corrected.snapshot().recording;
    assert.equal(ended.active,false); assert.equal(ended.reason,"time-limit");
    assert.equal(ended.frameCount,2);
    assert.ok(fs.readFileSync(ended.file).includes(Buffer.from('"reason":"time-limit"')));
    if (layer === "application") assert.equal(read(ended.file)[0].stamp,receivedAt,"original packet timestamp is retained");
    corrected.shutdown();
  }
  // Host timestamps are comparable across Node/Python; reject buffered pre-start
  // bytes even if their wall time is in the future, retain rollback timestamps.
  for (const layer of ["can", "application"]) {
    const clocked = createDiagnosticCanRecorder({
      chooseFile: async () => path.join(scratch, `membership-${layer}.pcapng`),
      nowFn: () => 10000, elapsedNowFn: () => 0, captureNowUsFn: () => 2000000,
      setTimeoutFn: () => 1, clearTimeoutFn: () => {},
    });
    await clocked.handle({action:"start",simulation:true,layer});
    const receive = extra => {
      const observation = {timestampUs:20000000,monotonicClock:CAPTURE_CLOCK,monotonicTimestampUs:3000000,...extra};
      if (layer === "application") clocked.onPdu({simulation:true,pdu:{...pdu,...observation}});
      else clocked.onFrame({simulation:true,frame:{canId:0x7e8,extended:false,dataHex:"0102",timestampSource:"host-chunk-arrival",...observation}});
    };
    receive({monotonicTimestampUs:1999999});
    receive({startedMonotonicTimestampUs:1999999});
    assert.equal(clocked.snapshot().recording.frameCount,0,"pre-start observation/partial response cannot enter a new recording");
    receive({timestampUs:5000000,startedTimestampUs:15000000,startedMonotonicTimestampUs:2000000});
    assert.equal(clocked.snapshot().recording.frameCount,1,"a response spanning wall-clock rollback is retained");
    await clocked.handle({action:"stop"});
    const [packet] = read(clocked.snapshot().recording.file, layer);
    assert.equal(packet.stamp,5000000); assert.equal(packet.metadata.startedTimestampUs,15000000);
    assert.equal(packet.metadata.startedMonotonicTimestampUs,2000000);
    clocked.shutdown();
  }
  for (const invalid of [{monotonicClock:CAPTURE_CLOCK}, {monotonicTimestampUs:1},
    {monotonicClock:CAPTURE_CLOCK,monotonicTimestampUs:true},
    {monotonicClock:CAPTURE_CLOCK,monotonicTimestampUs:1,startedMonotonicTimestampUs:2}]) {
    assert.equal(validCaptureClock(invalid),false);
    assert.equal(validDiagnosticPdu({...pdu,...invalid}),false);
  }
  const mismatch = createDiagnosticCanRecorder({chooseFile:async()=>path.join(scratch,"clock-mismatch.pcapng")});
  await mismatch.handle({action:"start",simulation:true,layer:"application"});
  mismatch.onPdu({simulation:true,pdu:{...pdu,monotonicClock:CAPTURE_CLOCK === "windows-qpc" ? "darwin-continuous" : "windows-qpc",monotonicTimestampUs:1}});
  assert.equal(mismatch.snapshot().recording.reason,"capture-clock-mismatch"); mismatch.shutdown();
  console.log("PASS application PDU: exact payloads/TLV/linktype/timestamps/context, short writes/close/exclusive files, explicit interval and source changes (software only)");
} finally { fs.rmSync(scratch,{recursive:true,force:true}); }
