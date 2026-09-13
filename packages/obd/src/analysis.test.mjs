import test from 'node:test';
import assert from 'node:assert/strict';
import { AT_HANDSHAKE } from '@porsche981/domain';
import { ProductionService, memoryPersist } from './production.mjs';
import { MOCK_ADAPTERS, cannedMockScript, pci } from './queue.mjs';
function fixture(overrides={}) {
  const writes=[], persist=memoryPersist(); let now=Date.now();
  const defaults=new Map(cannedMockScript().map(s=>[s.command,s.raw]));
  const svc=new ProductionService({persist,now:()=>now,listAdapters:async()=>MOCK_ADAPTERS,transportFactory:()=>({close(){},async request(command){writes.push(command); const v=overrides[command]??defaults.get(command)??'NO DATA\r>'; return typeof v==='function'?v():v;}})});
  return {svc,persist,writes, advance:ms=>now+=ms, async ready(){await svc.selectAdapter(MOCK_ADAPTERS[0]);await svc.connect();await svc.pollStatus();writes.length=0;}};
}
const select=(parameterId,ecu='7E8')=>({moduleKey:`obd-can:${ecu}`,parameterId});
test('analysis decodes public values and updates identities, never writes ECU',async()=>{
  const f=fixture(); await f.ready();
  const batch=await f.svc.readAnalysis([select('rpm'),select('coolant'),select('voltage')]);
  assert.deepEqual(batch.values.map(v=>v.value),[2000,88,12.34]);
  assert.ok(batch.values.every(v=>v.status==='valid'&&v.observedAt));
  assert.equal((await f.svc.listEcus()).length,1);
  assert.ok(f.writes.every(c=>c.startsWith('01')||c.startsWith('09')));
  assert.ok(batch.evidence.length>0);
});
test('per ECU support cannot borrow another ECU and repeated PID uses one request',async()=>{
  const both=(a,b)=>pci(a).replace('>','')+pci(b,'7E9');
  const f=fixture({'0100':both([0x41,0,0,0x10,0,0],[0x41,0,0,0x10,0,0]),'010C':both([0x41,0xc,0x1f,0x40],[0x41,0xc,0xf,0xa0])});
  await f.ready(); const b=await f.svc.readAnalysis([select('rpm'),select('rpm','7E9'),select('coolant','7E9')]);
  assert.deepEqual(b.values.map(v=>v.value),[2000,1000,null]);
  assert.equal(b.values[2].status,'unsupported'); assert.equal(f.writes.filter(c=>c==='010C').length,1); assert.ok(!f.writes.includes('0105'));
});
test('analysis enforces cap, unsupported requests and stale link before IO',async()=>{
  const f=fixture(); await f.ready();
  for(const s of [Array(9).fill(select('rpm')), [select('04')], [{moduleKey:'dme',parameterId:'rpm'}]]) await assert.rejects(f.svc.readAnalysis(s));
  assert.equal(f.writes.length,0); f.advance(9000); await assert.rejects(f.svc.readAnalysis([select('rpm')])); assert.equal(f.writes.length,0);
});
test('missing targeted reply is failure, not last value or another ECU value',async()=>{
  const f=fixture({'0105':pci([0x41,5,128],'7E9')}); await f.ready();
  const b=await f.svc.readAnalysis([select('coolant')]); assert.equal(b.values[0].status,'failed');assert.equal(b.values[0].value,null);
  const chained=fixture({'0120':'NO DATA\r>'}); await chained.ready();
  const v=await chained.svc.readAnalysis([select('voltage')]);
  assert.equal(v.values[0].status,'failed'); // Missing page 20 cannot establish absence on page 40.
  assert.ok(!chained.writes.includes('0140')); assert.ok(!chained.writes.includes('0142'));
});
test('transport failure quarantines cycle and no later command is sent',async()=>{
  const f=fixture({'0105':()=>{throw new Error('timeout');}}); await f.ready();
  await assert.rejects(f.svc.readAnalysis([select('coolant'),select('rpm')]),/timeout/);
  assert.equal(f.writes.at(-1),'0105');assert.equal(f.svc.snapshot().adapterConnected,false);
  const n=f.writes.length;await assert.rejects(f.svc.readAnalysis([select('rpm')]));assert.equal(f.writes.length,n);
});
test('VIN change during cycle discards result and original fault association',async()=>{
  let reads=0;const f=fixture({'0902':()=>pci([0x49,2,1,...Buffer.from(++reads===1?'WP0ZZZ98ZES000001':'WP0ZZZ98ZES000002')])});await f.ready();
  await assert.rejects(f.svc.readAnalysis([select('rpm')]),/身份/);assert.equal(f.svc.vehicleKey,null);assert.equal(f.svc.lastScan,null);
});
test('exclusive lock rejects scan and clear while analysis cycle awaits',async()=>{
  let release;const pending=new Promise(r=>release=r);const f=fixture({'0105':()=>pending});await f.ready();
  const task=f.svc.readAnalysis([select('coolant')]);await new Promise(r=>setImmediate(r));
  await assert.rejects(f.svc.scanFaults(),/busy/);await assert.rejects(f.svc.clearDtcs(),/busy/);
  release(pci([0x41,5,100]));await task;assert.ok(!f.writes.includes('04'));
});
