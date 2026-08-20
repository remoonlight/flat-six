/**
 * Self-check: standalone X-ray tune window wiring.
 * Run: node apps/desktop/electron/xray-tune-window.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function mustInclude(file, needle) {
  const text = fs.readFileSync(path.join(root, file), "utf8");
  if (!text.includes(needle)) {
    throw new Error(`${file} missing ${JSON.stringify(needle)}`);
  }
}

mustInclude("electron/main.mjs", "window:openXrayTune");
mustInclude("electron/main.mjs", "openXrayTuneWindow");
mustInclude("electron/main.mjs", "xray-tune?asset=");
mustInclude("electron/main.mjs", "xray-tune?scene=garage");
mustInclude("electron/preload.cjs", "openXrayTuneWindow");
mustInclude("src/pages/XrayTunePage.tsx", "data-page=\"xray-tune\"");
mustInclude("src/pages/XrayTunePage.tsx", "tuneAssetFromHash");
mustInclude("src/pages/XrayTunePage.tsx", "tuneGarageFromHash");
mustInclude("src/App.tsx", "isXrayTuneHash");
mustInclude("src/pages/LocatorPage.tsx", "单独微调窗口");
mustInclude("src/pages/LocatorPage.tsx", "openXrayTuneWindow?.(assetId");
mustInclude("src/pages/PartsBrowserPage.tsx", "scene: \"garage\"");
mustInclude("src/components/XrayTransformSliders.tsx", "uniformScale");

console.log("xray-tune-window.selfcheck: OK");
