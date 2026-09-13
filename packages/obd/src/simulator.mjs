import { assertReadCommand, OBD_LIVE_COMMANDS } from "@porsche981/domain";

export function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("cancelled"));
    const abort = () => { clearTimeout(timer); reject(new Error("cancelled")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}
export function canText(payload, ecu = "7E8") {
  const frames = [];
  if (payload.length <= 7) frames.push([payload.length, ...payload]);
  else {
    frames.push([0x10 | (payload.length >> 8), payload.length & 255, ...payload.slice(0, 6)]);
    for (let i = 6, seq = 1; i < payload.length; i += 7, seq++) frames.push([0x20 | (seq & 15), ...payload.slice(i, i + 7)]);
  }
  return frames.map((bytes) => ecu + " " + bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ")).join("\r");
}
/** This module has no serial, Bluetooth, socket or hardware SDK imports. */
export class SimulatedAdapter {
  constructor(scenario, wait = delay) { this.scenario = scenario; this.wait = wait; this.count = 0; }
  async request(command, signal) {
    assertReadCommand(command);
    this.count++;
    await this.wait(this.scenario === "no-response" ? 800 : 140, signal);
    if (this.scenario === "disconnect" && this.count >= 5) throw new Error("disconnected");
    if (this.scenario === "no-response") throw new Error("timeout");
    if (this.scenario === "malformed" && command === "010C") return "7E8 04 41 0C FF\r>";
    const clean = this.scenario === "no-codes";
    const rpm = Math.round(800 + Math.sin(this.count / 3) * 55);
    const bytes = {
      "0100": [0x41, 0, 0], // constructed below to match actual simulated PIDs
      "03": [0x43, clean ? 0 : 3, clean ? 0 : 1],
      "07": [0x47, 0, 0], "0A": [0x4a, 0, 0],
      "0101": [0x41, 1, clean ? 0 : 0x81, 0x07, 0xe5, 0],
      "010C": [0x41, 0x0c, (rpm * 4) >> 8, (rpm * 4) & 255],
      "0105": [0x41, 5, 125], "010D": [0x41, 0x0d, 0],
      "0104": [0x41, 4, 45], "010F": [0x41, 0x0f, 66], "0110": [0x41, 0x10, 1, 94],
      "020200": [0x42, 2, 0, clean ? 0 : 3, clean ? 0 : 1],
      "020C00": [0x42, 0x0c, 0, 0x20, 0xd0], "020500": [0x42, 5, 0, 130],
      "0902": [0x49, 2, 1, ...Array.from("WP0ZZZ98ZES000001", (c) => c.charCodeAt(0))],
    };
    const bits = [0, 0, 0, 0];
    for (const pid of [1, ...OBD_LIVE_COMMANDS.map((c) => parseInt(c.slice(2), 16))]) bits[(pid - 1) >> 3] |= 1 << (7 - ((pid - 1) % 8));
    bytes["0100"] = [0x41, 0, ...bits];
    if (clean && command.startsWith("02")) return `${command}\rNO DATA\r>`;
    return `${command}\r${canText(bytes[command])}\r>`;
  }
}
