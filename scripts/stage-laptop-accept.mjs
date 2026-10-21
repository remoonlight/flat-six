/** Stage a portable, software-only Windows acceptance kit from installed dependencies. */
import fs from "node:fs";
import path from "node:path";
import { inventory, verifyLaptopAcceptanceInputs, verifyAcceptanceKit } from "./laptop-accept-inputs.mjs";

const root = path.resolve(import.meta.dirname, "..");
const [destination, program, definitions, ...extra] = process.argv.slice(2);
if (!destination || !program || !definitions || extra.length) throw new Error("Supply a NEW private kit directory, current Windows program directory and matching definitions ZIP");
const output = path.resolve(destination), relative = path.relative(path.join(root, ".local"), output);
if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || fs.existsSync(output)) throw new Error("New directory below project .local required");
for (let ancestor = path.dirname(output); path.relative(root, ancestor) !== ""; ancestor = path.dirname(ancestor)) {
  if (ancestor === path.dirname(ancestor)) throw new Error("Acceptance output must remain below the project root");
  if (fs.existsSync(ancestor) && fs.lstatSync(ancestor).isSymbolicLink()) throw new Error("Acceptance output ancestor cannot be a symlink");
}
const inputs = verifyLaptopAcceptanceInputs(path.resolve(program), path.resolve(definitions));
const packages = ["playwright", "playwright-core"].map(name => ({ name,
  metadata: JSON.parse(fs.readFileSync(path.join(root, "node_modules", name, "package.json"), "utf8")) }));
if (packages[0].metadata.dependencies?.["playwright-core"] !== packages[1].metadata.version
  || packages[0].metadata.version !== packages[1].metadata.version) throw new Error("Installed Playwright dependencies do not match");
for (const relative of ["apps/desktop/electron/installed-entry.mjs", "apps/desktop/electron/offline-diagnostics.mjs"])
  if (!fs.readFileSync(path.join(root, relative)).equals(fs.readFileSync(path.join(path.resolve(program), "resources/app", relative)))) throw new Error("Source/installed acceptance helper mismatch");
fs.mkdirSync(output, { recursive: true });
function copy(relative) {
  const source = path.join(root, relative), target = path.join(output, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true, filter: file => {
    if (fs.lstatSync(file).isSymbolicLink()) throw new Error("Acceptance kit excludes symlinks");
    return true;
  } });
}
for (const relative of ["scripts/windows-runtime-accept.mjs", "scripts/windows-runtime-source.mjs", "scripts/laptop-accept-inputs.mjs", "scripts/obd-unidentified-ui-check.mjs",
  "apps/desktop/electron/installed-entry.mjs", "apps/desktop/electron/offline-diagnostics.mjs"]) copy(relative);
for (const { name } of packages) copy("node_modules/" + name);
// ASCII/CRLF launcher works with quoted program/ZIP paths, including spaces.
fs.writeFileSync(path.join(output, "accept-laptop.cmd"), fs.readFileSync(path.join(root, "scripts/accept-laptop.cmd"), "utf8").replace(/\r?\n/g, "\r\n"));
fs.writeFileSync(path.join(output, "package.json"), JSON.stringify({ name: "flat-six-laptop-acceptance", private: true, type: "module" }) + "\n");
fs.writeFileSync(path.join(output, "README.md"), `# Windows 笔记本独立软件验收\n\n将本工具包、当前程序与匹配资料ZIP分别复制到最终Windows笔记本。无需开发仓库、npm安装、系统Node/Python或浏览器下载，使用程序内置Node和现有Electron。先运行：\n\n\`\`\`bat\naccept-laptop.cmd "D:\\Flat Six" "D:\\definitions-live-current.zip" --check-only\naccept-laptop.cmd "D:\\Flat Six" "D:\\definitions-live-current.zip"\n\`\`\`\n\n第一条仅校验文件、安装入口和绑定，不证明Windows程序已启动或资料已导入。第二条从FlatSix.exe实际启动，清除开发PATH/诊断环境，用新的隔离用户目录检验SQLite、内置运行时、资料导入、标准模拟、后台厂商演练、停止/桌面参数PCAPNG保存及设码/恢复演练（不恢复已移除的日常演练按钮）；默认总时限五分钟。结果写入本工具包的.local/installed-ui-*，保留result.json及失败输出。连接列表是空夹具且禁止车辆传输；软件通过不代表诊断头、实际车辆、十分钟耐久或写入资格。此工具不加载设备DLL、不连接PIWIS主机，不更改正常应用用户资料。\n\n程序清单SHA-256：${inputs.deliveryManifestSha256}\n\n资料ZIP SHA-256：${inputs.definitionZipSha256}\n\n本工具绑定上述两份交付；任一变化需重新生成工具包，不能使用旧绑定静默回退。Playwright/Playwright-core ${packages[0].metadata.version}来自项目已安装依赖，Apache-2.0许可证及通知保留在对应node_modules目录。\n`);
const files = inventory(output);
fs.writeFileSync(path.join(output, "laptop-accept-manifest.json"), JSON.stringify({ kind: "flat-six-laptop-acceptance-v1",
  programIncluded: false, privateDefinitionsIncluded: false, hardwareAccessEnabled: false,
  deliveryManifestSha256: inputs.deliveryManifestSha256, definitionZipSha256: inputs.definitionZipSha256,
  electronVersion: inputs.electronVersion, playwrightVersion: packages[0].metadata.version, files }, null, 2) + "\n");
verifyAcceptanceKit(output);
verifyLaptopAcceptanceInputs(path.resolve(program), path.resolve(definitions), output);
console.log(JSON.stringify({ ...inputs, ok: true, output, kitFiles: files.length, kitBound: true,
  bytes: files.reduce((sum, row) => sum + row.bytes, 0), staticOnly: true, windowsExecutionVerified: false }));
