# Porsche 981 Garage

面向 **2014 Porsche Boxster S（981）PDK** 的本机 Windows Electron 车库，包含零件浏览、维护状态、只读 OBD 会话、车辆设置与部件定位。

本项目是 [FLAT·SIX](https://www.flat-six.org/) 开源仓库 [`dmitry-grechko/flat-six`](https://github.com/dmitry-grechko/flat-six)（MIT）的 fork 开发分支：[`remoonlight/flat-six: porsche981`](https://github.com/remoonlight/flat-six/tree/porsche981)。坐标系与幽灵车壳约定沿用 flat-six X-ray；本分支原创应用代码采用 [PolyForm Noncommercial 1.0.0](LICENSE)。

当前仅支持源码自行部署和开发运行，**不提供安装器、electron-builder 包或其他打包发行物**。

## 使用方法

### Windows 一键启动（推荐）

前置条件：

- Windows 10/11
- Node.js **≥ 20**，并已加入 `PATH`
- 已 clone 本仓库

在资源管理器中双击：

```text
scripts\run-desktop.cmd
```

该启动器是纯 CMD 脚本，不使用隐藏 PowerShell，便于杀毒软件检查。它会：

1. 切换到仓库根目录；
2. 清理遗留的 Vite `5173` 监听进程，以及命令行中属于 `porsche981` 的 Node/Electron 进程；
3. 在缺少 `node_modules` 时执行 `npm install`；
4. 未设置时补充 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`；
5. 最小化启动 `npm run dev`。

最终使用的是弹出的 **Electron UI**；最小化的控制台只是开发服务。

更方便的做法是在桌面创建快捷方式：

- 目标：`D:\code\porsche981\scripts\run-desktop.cmd`
- “起始位置”可留空；脚本通过 `cd /d "%~dp0.."` 自动进入仓库根目录

也可以在桌面新建 `.bat` 调用启动器，例如仓库相对桌面位于 `..\code\porsche981` 时：

```bat
@echo off
call "%~dp0..\code\porsche981\scripts\run-desktop.cmd"
```

仓库实际位于 `D:\code\porsche981` 时，优先使用上面的快捷方式，避免桌面盘符或目录布局不同导致相对路径失效。

相关脚本：

- `scripts\run-desktop.cmd`：推荐的一键启动入口
- `scripts\run-desktop.ps1`：PowerShell 启动入口
- `scripts\kill-dev-related.ps1`：清理相关开发进程

### 手动开发启动

在仓库根目录打开 PowerShell：

```powershell
npm install
npm run ingest:x431   # 可选：导入已有 X431 明文数据
npm run build
npm test
npm run dev
```

如果 Electron 二进制下载慢或超时，可在执行 `npm install` 前设置镜像：

```powershell
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npm install
```

开发数据库位于 `.local/garage.db`（已 gitignore）。SQLite 由系统 Node 子进程 `apps/desktop/electron/db-bridge.mjs` 访问。

可选完整验收：

```powershell
npm run accept:all
```

PETKA、CMS、mesh-map 等数据状态命令见 [`package.json`](package.json)。

## 3D 资产

| 资产 | 入库状态 | 说明 |
|------|----------|------|
| flat-six 车身、座舱与系统件 | 已入 Git | `.local/flat-six/**/*.glb`；CC BY，详见上游与 NOTICE |
| PETKA 图号分件及 merged 模型 | 已入 Git | `.local/petka-models/**/*.glb` |
| CMS 991 引擎/底盘抠模 | **不入库** | 仅限本机从正版游戏导出；产品不使用 991 车身 |
| PETKA 本机价格 CSV | 已入 Git | `data/petka/*/parts.csv`（保养子集；导入 `npm run ingest:petka`） |
| X-ray 姿态 JSON | seed 模板入库 | 恢复方式见 [`data/seed/xray/README.md`](data/seed/xray/README.md) |

GLB 以普通 Git blob 保存，不使用 Git LFS；公开 fork 首次 clone 会因此增加约 **450 MB** 下载量。单个已提交 GLB 均低于 GitHub 100 MB 限制。

## 硬边界

- **不写 ECU**：OBD、设码与诊断仅只读（ADR 001）
- **仅本机运行**：Windows + SQLite，无云服务、无账号（ADR 002）
- PETKA GUI 默认不自动操作；仅在当前会话得到明确授权后使用（ADR 003）
- 不破解、不解析、不提交 PETKA `DATA\PO` 或 `.zgd`
- CMS 抠模仅保存在本机，不重新分发；产品不使用 991 车身（ADR 004）
- UI 价格统一折算为 CNY；teile / Design911 数据不视为 PETKA 已核价格

## 文档

| 文档 | 用途 |
|------|------|
| [`docs/requirements.md`](docs/requirements.md) | 权威业务需求 |
| [`docs/progress.md`](docs/progress.md) | 当前进度与已确定结论 |
| [`docs/obd-plan.md`](docs/obd-plan.md) | 实时 OBD 分期计划 |
| [`docs/adr/`](docs/adr/) | 不写 ECU、仅本地、PETKA GUI、CMS 本机资产等决策 |
| [`docs/cms-991-engine-rip.md`](docs/cms-991-engine-rip.md) | CMS 991 引擎/底盘本机资产说明 |
| [`data/petka/README.md`](data/petka/README.md) | PETKA 明文采集与 inbox 约定 |
| [`data/seed/flat-six/README.md`](data/seed/flat-six/README.md) | flat-six 车身与座舱数据 |
| [`data/seed/xray/README.md`](data/seed/xray/README.md) | X-ray 拼装与姿态模板 |
| [`data/seed/fx.json`](data/seed/fx.json) | UI 折算 CNY 使用的手工汇率 |
| [`LICENSE`](LICENSE) | PolyForm Noncommercial 1.0.0 |

## 项目结构

```text
apps/desktop/     Electron 主进程、preload 与 React UI
packages/domain/  间隔、定位、价格标记、CSV 等纯业务逻辑
packages/db/      SQLite schema 与查询
data/petka/       PETKA 明文导出与 zone 价 CSV（`*/parts.csv`）
data/seed/        间隔、定位、线束、X-ray、flat-six、CMS、X431 种子
scripts/          启动、导入、验收、监视与状态脚本
docs/             需求、进度、研究记录与 ADR
.local/           数据库、缓存和本机 3D 资产
```

## 许可证与第三方材料

仓库作者原创代码采用 **[PolyForm Noncommercial License 1.0.0](LICENSE)**，仅许可非商业用途。

以下第三方或来源材料不属于该许可的授权范围，研究和二次使用责任由使用者自行承担：

- DTC 手册译文与相关种子
- teile / Design911 等公开价格抓取结果
- X431 导出与设码明文归档
- flat-six、PETKA、游戏或其他第三方 3D 资产
