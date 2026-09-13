import { spawn } from "node:child_process";
import { CommandQueue } from "./queue.mjs";

/** Async stdin + serial read. Peek() must not be used (it blocks redirected stdin). */
export const SERIAL_HOST_PS = `
$ErrorActionPreference = 'Stop'
$portName = $env:PORSCHE981_COM
$baud = [int]$env:PORSCHE981_BAUD
$p = New-Object System.IO.Ports.SerialPort $portName, $baud
$p.NewLine = "\`r"
$p.ReadTimeout = 40
$p.WriteTimeout = 2000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false))
$readTask = $stdin.ReadLineAsync()
Write-Output '{"kind":"ready"}'
try {
  while ($true) {
    while ($p.BytesToRead -gt 0) {
      $chunk = $p.ReadExisting()
      if ($chunk) { Write-Output (('{"kind":"data","text":' + ($chunk | ConvertTo-Json -Compress) + '}')) }
    }
    if ($readTask.IsCompleted) {
      $line = $readTask.Result
      if ($null -eq $line) { break }
      $msg = $line | ConvertFrom-Json
      if ($msg.op -eq 'write') { $p.Write([string]$msg.data) }
      elseif ($msg.op -eq 'close') { break }
      $readTask = $stdin.ReadLineAsync()
    } else {
      Start-Sleep -Milliseconds 15
    }
  }
} finally {
  try { $p.Close() } catch {}
}
`;

export function attachJsonLineProtocol(proc, { listeners, onReady, onDead }) {
  let buf = "";
  proc.stdout.on("data", (d) => {
    buf += d;
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      let msg; try { msg = JSON.parse(line); } catch { continue; }
      if (msg.kind === "ready") onReady();
      else if (msg.kind === "data") for (const fn of listeners) fn(msg.text ?? "");
    }
  });
  proc.on("error", (e) => onDead(e));
  proc.on("exit", () => onDead(new Error("disconnected")));
}

export class PowerShellSerialTransport {
  constructor(adapter, { baud = 38400, spawnPs = spawn } = {}) {
    if (!/^COM[1-9]\d*$/i.test(adapter.port) || !Number.isInteger(baud) || baud <= 0) throw new Error('obd_invalid_port');
    this.closed = false;
    this.listeners = new Set();
    this.proc = spawnPs("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", SERIAL_HOST_PS], {
      windowsHide: true,
      env: { ...process.env, PORSCHE981_COM: adapter.port, PORSCHE981_BAUD: String(baud) },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let settled = false;
    this.ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.close();
        reject(new Error("obd_serial_start_timeout"));
      }, 8000);
      let diagnostic = '';
      this.proc.stderr?.on("data", d => { diagnostic = (diagnostic + d).slice(-2000); });
      attachJsonLineProtocol(this.proc, {
        listeners: this.listeners,
        onReady: () => { if (!settled) { settled = true; clearTimeout(timer); resolve(); } },
        onDead: (e) => {
          if (!settled) { settled = true; clearTimeout(timer); reject(new Error(diagnostic || e?.message || 'disconnected')); }
          this.queue?.poison?.(e?.message || "disconnected");
        },
      });
    });
    this.ready.catch(() => {});
    this.queue = new CommandQueue({
      write: async (data) => {
        await this.ready;
        if (!this.proc.stdin.writable) throw new Error("disconnected");
        this.proc.stdin.write(JSON.stringify({ op: "write", data }) + "\n");
      },
      subscribe: (fn) => { this.listeners.add(fn); return () => this.listeners.delete(fn); },
      onQuarantine: () => this.close(),
    });
  }
  async request(command, timeoutMs) {
    await this.ready;
    if (this.closed) throw new Error('disconnected');
    return this.queue.request(command, timeoutMs);
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.queue?.close();
    try { this.proc.stdin.write(JSON.stringify({ op: "close" }) + "\n"); } catch { /* */ }
    try { this.proc.kill(); } catch { /* */ }
  }
}
