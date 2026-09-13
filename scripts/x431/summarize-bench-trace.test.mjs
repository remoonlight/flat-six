import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeObservedSingleFrame, summarizeTrace } from './summarize-bench-trace.mjs';

const request = { length: 19, hex: '64 00 01 ff 02 0d 61 01 08 fc 00 03 22 f1 9e aa aa aa aa' };
test('extracts payload without mistaking wrapper, PCI or padding for diagnostic bytes', () => {
  const decoded = decodeObservedSingleFrame(request);
  assert.equal(decoded.diagnosticPayload, '22 F1 9E');
  assert.equal(decoded.candidateCanId, '0x7E0');
  assert.equal(decoded.encodedAddress, 'FC 00');
});
test('rejects status packets, truncation, other wrappers and multi-frame PCI', () => {
  for (const bad of [
    { length: 2, hex: 'ff 02' }, { ...request, length: 18 },
    { ...request, hex: request.hex.replace('64 00', '65 00') },
    { ...request, hex: request.hex.replace('00 03 22', '00 10 22') },
    { ...request, hex: request.hex.replace('fc 00', 'fc 01') },
  ]) assert.equal(decodeObservedSingleFrame(bad), null);
});
test('pairs transport status without declaring ECU success or exposing process arguments', () => {
  const source = [
    { type: 'send', payload: { event: 'enter', seq: 1, call: 1, time: 100, name: '_Z40ApkApi_SendDataToSmartBoxGetBinaryStringPhiS_ii', vci_tx: request, args: ['private-pointer'] } },
    { type: 'send', payload: { event: 'leave', call: 1, time: 700, return_int: 2, vci_rx: { length: 2, hex: 'ff 02' } } },
  ].map(JSON.stringify).join('\n');
  const result = summarizeTrace(source);
  assert.equal(result.diagnosticRequests[0].vciReturnBytes, 'FF 02');
  assert.equal(result.diagnosticRequests[0].durationMs, 600);
  assert.equal(result.vehicleResponse, null);
  assert.equal(result.productionReady, false);
  assert.equal(JSON.stringify(result).includes('private-pointer'), false);
});
