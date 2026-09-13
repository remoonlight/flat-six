import { ElmPromptBuffer, assertTrustedCommand, AT_HANDSHAKE } from "@porsche981/domain";

/** Timeout/overflow poisons the transport: no further writes until a new queue/reconnect. */
export class CommandQueue {
  constructor({ write, subscribe, now = () => Date.now(), onQuarantine = () => {} }) {
    this.write = write;
    this.now = now;
    this.quarantined = false;
    this.closed = false;
    this.buffer = new ElmPromptBuffer();
    this.waiters = [];
    this.onQuarantine = onQuarantine;
    this.unsubscribe = subscribe((chunk) => this.onChunk(chunk));
  }
  poison(reason) {
    if (this.quarantined && !this.waiters.length) return;
    this.quarantined = true;
    this.buffer = new ElmPromptBuffer();
    const waiters = this.waiters;
    this.waiters = [];
    for (const w of waiters) w.reject(new Error(reason));
    this.onQuarantine(reason);
  }
  onChunk(chunk) {
    if (this.quarantined || this.closed) return;
    let frames;
    try { frames = this.buffer.push(String(chunk)); }
    catch {
      this.poison("obd_response_too_large");
      return;
    }
    for (const frame of frames) {
      const w = this.waiters[0];
      if (!w) continue;
      w.resolve(frame);
      this.waiters = this.waiters.filter((x) => x !== w);
    }
  }
  request(command, timeoutMs = 2000) {
    const cmd = assertTrustedCommand(command);
    const run = () => new Promise((resolve, reject) => {
      if (this.closed) return reject(new Error("disconnected"));
      if (this.quarantined) return reject(new Error("obd_transport_quarantined"));
      this.buffer = new ElmPromptBuffer();
      const timer = setTimeout(() => {
        this.poison("timeout");
      }, timeoutMs);
      this.waiters.push({
        command: cmd,
        resolve: (frame) => { clearTimeout(timer); resolve(frame); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      Promise.resolve().then(() => this.write(cmd + "\r")).catch((e) => {
        clearTimeout(timer);
        this.poison(e.message || "disconnected");
      });
    });
    const job = this.chain ? this.chain.then(run, run) : run();
    this.chain = job.then(() => {}, () => {});
    return job;
  }
  close() {
    this.closed = true;
    this.poison("disconnected");
    this.unsubscribe?.();
  }
}

export class ScriptedTransport {
  constructor(script, { repeat = true } = {}) {
    this.script = script;
    this.repeat = repeat;
    this.listeners = new Set();
    this.offset = 0;
    this.writes = [];
    this.queue = new CommandQueue({
      write: async (cmd) => {
        if (this.queue.quarantined) throw new Error("obd_transport_quarantined");
        const key = cmd.replace(/\r/g, "");
        this.writes.push(key);
        const step = this.script.find((s, i) => i >= this.offset && s.command === key)
          ?? (this.repeat ? this.script.find((s) => s.command === key) : null);
        if (!step) {
          queueMicrotask(() => this.emit("?\r>"));
          return;
        }
        this.offset = this.script.indexOf(step) + 1;
        const chunks = step.chunks ?? [step.raw ?? ">\r"];
        const send = () => {
          if (this.queue.quarantined) return;
          for (const c of chunks) this.emit(c);
        };
        if (step.disconnect) { queueMicrotask(() => this.close()); return; }
        if (step.delayMs) setTimeout(send, step.delayMs);
        else queueMicrotask(send);
      },
      subscribe: (fn) => { this.listeners.add(fn); return () => this.listeners.delete(fn); },
      onQuarantine: () => {},
    });
  }
  emit(chunk) { for (const fn of this.listeners) fn(chunk); }
  request(command, timeoutMs) { return this.queue.request(command, timeoutMs); }
  close() { this.queue.close(); }
}

export function pci(payload, ecu = "7E8") {
  const frames = [];
  if (payload.length <= 7) frames.push([payload.length, ...payload]);
  else {
    frames.push([0x10 | (payload.length >> 8), payload.length & 255, ...payload.slice(0, 6)]);
    for (let i = 6, seq = 1; i < payload.length; i += 7, seq++) frames.push([0x20 | (seq & 15), ...payload.slice(i, i + 7)]);
  }
  return frames.map((b) => ecu + " " + b.map((x) => x.toString(16).toUpperCase().padStart(2, "0")).join(" ")).join("\r") + "\r>";
}

export function cannedMockScript() {
  const vin = [0x49, 2, 1, ...Array.from("WP0ZZZ98ZES000001", (c) => c.charCodeAt(0))];
  const cal = [0x49, 4, 1, ...Array.from("DME-CAL-981".padEnd(16), (c) => c.charCodeAt(0))];
  return [
    ...AT_HANDSHAKE.map(command => ({command, raw:command === 'ATZ' ? 'ELM327 v2.3\r>' : 'OK\r>'})),
    { command: "ATRV", raw: "12.41V\r>" },
    { command: "0100", raw: pci([0x41, 0, 0xff, 0xff, 0xff, 0xff]) },
    { command: "0120", raw: pci([0x41, 0x20, 0, 0, 0, 0x19]) },
    { command: "0140", raw: pci([0x41, 0x40, 0x40, 0, 0, 0]) },
    ...[0x04,0x05,0x06,0x07,0x08,0x09,0x0b,0x0e,0x0f,0x11].map(pid => ({ command: `01${pid.toString(16).padStart(2,'0').toUpperCase()}`, raw: pci([0x41, pid, 128]) })),
    { command: '0110', raw: pci([0x41, 0x10, 0x01, 0x90]) },
    { command: '013C', raw: pci([0x41, 0x3c, 0x12, 0xc0]) },
    { command: '013D', raw: pci([0x41, 0x3d, 0x12, 0xc0]) },
    { command: "010C", raw: pci([0x41, 0x0c, 0x1f, 0x40]) },
    { command: "010D", raw: pci([0x41, 0x0d, 0]) },
    { command: "0142", raw: pci([0x41, 0x42, 0x30, 0x34]) },
    { command: "03", raw: pci([0x43, 1, 0x03, 0x01]) },
    { command: "07", raw: pci([0x47, 1, 0x05, 0x71]) },
    { command: "0A", raw: pci([0x4a, 1, 0x04, 0x20]) },
    { command: "0902", raw: pci(vin) },
    { command: "0904", raw: pci(cal) },
    { command: "0906", raw: pci([0x49, 6, 1, 0xde, 0xad, 0xbe, 0xef]) },
    { command: "04", raw: pci([0x44]) },
  ];
}

export const MOCK_ADAPTERS = [
  { port: "COM5", friendlyName: "vLinker FS BT (COM5)", pnpId: "BTHENUM\\VID_MOCK_VLINKER", occupied: false, occupiedReason: null, preferred: true },
  { port: "COM9", friendlyName: "Standard Serial over Bluetooth link (COM9) | OBDLink MX+", pnpId: "BTHENUM\\VID_MOCK_MX", occupied: true, occupiedReason: "MX+ 由 RaceChrono 占用，不自动选择", preferred: false },
];
