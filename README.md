# Porsche 981 Garage

2014 Boxster S（981）PDK 的 **本机 Windows** Electron 车库：零件浏览器、维护状态、实时 OBD（只读查码+会话）、车辆设置、部件定位。产品未成型，**不做打包分发**。

| 文档 | 用途 |
|------|------|
| [`docs/requirements.md`](docs/requirements.md) | 业务需求 v7（权威） |
| [`docs/progress.md`](docs/progress.md) | 交付成果 + 已拍板收敛结论 |
| [`docs/obd-plan.md`](docs/obd-plan.md) | 实时 OBD 分期 plan |
| [`docs/adr/`](docs/adr/) | ADR（不写车、仅本地、PETKA GUI、CMS 抠模本机） |
| [`docs/cms-991-engine-rip.md`](docs/cms-991-engine-rip.md) | CMS 991 引擎/底盘本机资产说明（不用 991 车身） |
| [`data/petka/README.md`](data/petka/README.md) | PETKA 采集 / inbox |
| [`data/seed/flat-six/README.md`](data/seed/flat-six/README.md) | flat-six 车身/座舱 |
| [`data/seed/xray/README.md`](data/seed/xray/README.md) | X-ray 拼装与姿态模板 |
| [`data/seed/fx.json`](data/seed/fx.json) | 手工汇率（UI 折算 CNY） |
| [`LICENSE`](LICENSE) | PolyForm Noncommercial 1.0.0 |

## 要求

- Windows
- Node.js **≥ 20**

Electron 二进制下载慢/超时可设镜像（PowerShell）：

```powershell
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
```

## 自行部署（开发）

```powershell
npm install
npm run ingest:x431
npm run build
npm run test
npm run dev
```

开发库：仓库内 `.local/garage.db`（gitignore）。SQLite 经系统 Node 子进程 `electron/db-bridge.mjs`。

## 开箱不可用（预期）

本仓是源码 + 种子，**不是**可开箱运行的整车数据盘：

| 缺口 | 说明 |
|------|------|
| 三维 GLB（约 718MB） | 在 `.local/`，**不进仓** → 部件定位 / X-ray 多为 placeholder |
| PETKA 本机价 | `data/petka/**/parts.csv` 等本机权威，gitignore |
| 姿态 JSON | 可从 seed 模板恢复到 `.local/`，见 [`data/seed/xray/README.md`](data/seed/xray/README.md) |

可选增强：`npm run accept:all`、PETKA / CMS / mesh-map 等 status 脚本（见 `package.json`）。

## 硬边界

- **不写 ECU**；OBD / 设码只读（ADR 001）
- **无云、无账号**；仅本机 Windows + SQLite（ADR 002）
- 不破解 / 不解析 PETKA `DATA\PO` / `.zgd`
- CMS 抠模仅本机；产品不用 991 车身

## 许可证与第三方材料

仓库作者原创代码采用 **[PolyForm Noncommercial License 1.0.0](LICENSE)**（非商业）。

以下材料**不在**该许可授权范围内，研究用途与二次使用自负：

- DTC 手册译文与相关种子
- teile / Design911 等公开价抓取结果（非 PETKA 已核）
- X431 导出与设码明文归档
- 游戏/第三方三维资产（若本机自行取得）

## 结构

```text
apps/desktop/     Electron + React UI
packages/domain/  间隔/定位/价标记/CSV 纯逻辑
packages/db/      SQLite 访问
data/petka/       PETKA 明文导出约定（本机价 gitignore）
data/seed/        间隔 / 定位 / 线束 / xray / flat-six / cms-rip / X431
scripts/          ingest / accept / watch / status
docs/             需求、进度、ADR
```
