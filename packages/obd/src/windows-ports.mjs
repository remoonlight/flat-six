import { spawn } from "node:child_process";

/** One-shot Windows COM enumeration. UTF-8. Read-only PnP parent/bus description. No port open. */
export function enumerateWindowsPorts({ spawnPs = spawn, platform = process.platform } = {}) {
  if (platform !== "win32") return Promise.resolve([]);
  const script = `
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = 'SilentlyContinue'
$rows = @()
Get-CimInstance Win32_PnPEntity | ForEach-Object {
  $m = [regex]::Match($_.Name, '\\((COM\\d+)\\)')
  if ($m.Success) {
    $desc = $null; $parentName = $null
    try {
      $desc = (Get-PnpDeviceProperty -InstanceId $_.PNPDeviceID -KeyName 'DEVPKEY_Device_BusReportedDeviceDesc' -ErrorAction SilentlyContinue).Data
      $parent = (Get-PnpDeviceProperty -InstanceId $_.PNPDeviceID -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
      if ($parent) { $parentName = (Get-PnpDevice -InstanceId $parent -ErrorAction SilentlyContinue).FriendlyName }
    } catch {}
    $label = $_.Name
    if ($desc) { $label = "$label | $desc" }
    if ($parentName) { $label = "$label | $parentName" }
    $rows += [pscustomobject]@{ port = $m.Groups[1].Value; friendlyName = $label; pnpId = $_.PNPDeviceID }
  }
}
$rows | ConvertTo-Json -Compress
`;
  return new Promise((resolve, reject) => {
    const ps = spawnPs("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], { windowsHide: true });
    let out = "", err = "";
    const timer = setTimeout(() => { ps.kill(); reject(new Error("obd_port_enum_timeout")); }, 8000);
    ps.stdout.on("data", (d) => { out += d; });
    ps.stderr.on("data", (d) => { err += d; });
    ps.on("error", reject);
    ps.on("exit", (code) => {
      clearTimeout(timer);
      if (code && !out.trim()) return reject(new Error(err || "obd_port_enum_failed"));
      const text = out.trim();
      if (!text) return resolve([]);
      try {
        const parsed = JSON.parse(text);
        const rows = Array.isArray(parsed) ? parsed : parsed ? [parsed] : [];
        resolve(rows.map((r) => ({ port: String(r.port), friendlyName: String(r.friendlyName ?? r.port), pnpId: r.pnpId ? String(r.pnpId) : null })));
      } catch (e) { reject(e); }
    });
  });
}
