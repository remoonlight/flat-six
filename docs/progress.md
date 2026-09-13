# 交付进度与收敛质疑（2026-08-01）

> 权威需求：`docs/requirements.md`（v7）  
> PETKA 采集：`data/petka/README.md`  
> ADR：`docs/adr/001-no-ecu-write.md`、`docs/adr/002-local-only-windows.md`、`docs/adr/003-petka-gui-explicit-only.md`、`docs/adr/004-cms-rip-local-only.md`

本文记录**当前工程成果**与已拍板的**收敛结论**。不替代需求正文。

### 2026-09-13 OBD 文档与代码收敛

统一 Markdown 入口到 `docs/README.md`：需求正文、使用说明、诊断交互细则、逐码候选清单、内部数据生成说明各有职责；旧分期和未来设码架构标为历史/待实现，不覆盖当前边界。README 与 ADR 对齐，清码是唯一写操作例外；完整诊断流程核查和实车验证继续保留为缺口。

清理数据分析重复样式，增加 `npm run accept:obd-analysis-ui`，忽略 Python 编译缓存。提交前重新通过 131 项测试、生产构建、新数据分析 Electron 验收及 3 项 X431 离线提取测试；其余三组相关界面验收沿用本轮已通过结果。本次提交包含本轮 OBD/X431 功能与已验证的 Windows 启动修复，不包含 `.local` 模型、缓存或本机配置。

### 2026-09-13 实时 OBD：统一故障库与数据分析

按最新确认实现故障标题「代码 + 问题」、独立勾选、默认折叠及三个详情标签；界面隐藏手册出处，内部保留证据。新增数据分析：故障需求汇总跳转、单元分类/搜索/点选、数据明细和实际值反馈。每批暂限 8 项（软件预算，不是适配器实测上限），超限和未支持项目不丢失；16 个基础 Mode 01 参数按各单元支持列表读取，值带时间/有效性和批次耗时。暂停、离页、超时及 VIN 变化隔离旧结果，单批串行，不并发争用连接。原车辆档案、清码、设码库与 X431 保留。

原件位于用户提供的 `Z:\porsche\981`。统一收录 1,106 个索引代码，1,081 个有诊断正文证据、25 个仍仅索引；939 个整理出规范化候选数据需求、167 个待建对应，共 50 类候选。已修复主要字体映射及标题归属错误，仍保留残字/跨页/适用性缺口；21 个索引外标题候选未自动入库。完整逐码检查步骤与厂商读取方法尚未完成核查，不能把候选数当作实车支持数。逐码 Markdown 与收敛质疑见 [数据需求清单](research/obd-manual-data-needs.md)。

验证：`npm test` 131 项通过（domain 94、DB 17、OBD 20），生产构建通过。新数据分析界面验收覆盖隐藏出处、完整需求、模拟反馈、超限、暂停/切页及人为延迟 IPC 的晚到结果隔离；生产故障/清码、981 设码/X431、旧模拟/回放三组回归通过。完整桌面 tsc 仍仅报告既存 `LocatorGlbViewer.tsx` 的 three 类型与空值错误；不计为全量类型检查通过。没有接触真实设备、车辆或日常库。

使用方法见 [实时 OBD 说明](obd-live-guide.md)，具体规则见 [确认需求](obd-fault-data-analysis-requirements.md)。

### 2026-09-13 实时 OBD：设备匹配、真实诊断流程与最新单元档案

按用户确认的六项需求改为设备匹配 / 故障码 / 车辆信息 / 设码 / 开发与验证；删除分析洞察，模拟/回放/实时数据/就绪/手工工具归入开发。标题常驻独立设备/车辆/点火/电压状态，过期数据不会继续作为有效状态。原 981 设码库及 X431 保留。

新增 Windows COM/ELM 传输、严格 CAN PCI/计数字段解析、串行队列、超时断开隔离、标准响应单元分组。真实读码与 Mode 04 清码有专用 IPC，清前落盘并确认当前 VIN、清后逐单元复读，永久/待定码仍显示，不确定写请求不重试。SQLite 保存每车每单元最新有效字段及变化日期，VIN 缺失或变化不混存；已保存车辆视图可重启回读。ADR 001 按本次用户授权开放清码例外，编码/刷写仍未开放。

验证：119 项单元/集成测试、6 项独立协议检查、生产构建、新生产 OBD 界面验收、原模拟/回放界面回归通过。真实 PowerShell 子进程通过虚拟串口流测试；界面使用临时库与明确标记的 mock，验证标定变化/序列号保留/唯一记录/重启。完整桌面 tsc 仅报告既存 3D 类型问题，无新增 OBD 报错。

能力限制：没有操作真实设备或车辆；只实现标准排放诊断，厂商全模块/硬件序列号/软件版本/编码读取仍待协议核实。504 条 981 索引仅 2 条有已核实检查要点，X431 默认顺序仅首屏 7 项有证据；这些缺项如实展示。操作与范围见 [实时 OBD 说明](obd-live-guide.md)。

### 2026-09-13 实时 OBD 设码页：981 功能库 + X431 保留

已按用户指定的 StormEye818 参考仓库固定提交导入 64 项 981 功能、129 个步骤。默认按本车显示 63 项候选（涡轮压力项隐藏且不可选），新增搜索、模块筛选、跨模块完整方案、日行灯配套检查、启停/运排替代方案、复制和逐步骤 X431 实测记录。原 X431 菜单及历史快照保留，新旧记录共用 SQLite。社区参考不标为本车验证，未增加车辆指令发送入口。来源、范围与操作方法见 [数据说明](../data/seed/coding-guide/README.md)。

验证：`npm test` 99 项通过，生产构建通过；`coding-guide-ui-accept` 用临时库验证 981 过滤、跨模块 7 步完整性、运排替代方案、复制、实际值不预填、旧 X431 菜单、新旧记录共用及应用重启后回读，并完成截图检查。桌面完整 TypeScript 检查仍有既存 `LocatorGlbViewer.tsx` 的 three 类型/空值报错；本次设码相关文件未报告类型错误。

### 2026-09-13 Windows 快捷方式启动修复

桌面快捷方式指向的 `scripts/run-desktop.cmd` 使用 LF 换行，Windows CMD 会截断 UTF-8 命令，尚未进入 npm 构建便退出。已恢复 CRLF，并通过 `.gitattributes` 保留批处理原始换行字节（含源码 ZIP）。启动清理改为识别当前仓库的 Electron 可执行文件、Node 入口及其子进程，保护启动器祖先进程，不再按项目名模糊匹配或直接终止 5173 端口占用者。

验证：原 CMD 启动链成功运行 Vite / Electron / DB bridge；生产构建通过；OBD Electron 界面回归通过（独立测试库，含保存、重开与回放）；清理成功移除测试启动的残留，并保留携带项目路径、占用 5173 的无关 Node 探针。

### 2026-09-13 X431 无车通信取证

已在用户授权的 rooted X431 上完成两次 981 DME 进入系统采集，各捕获 24 次 App → 诊断头调用，发送序列一致；获得 `10 89`、`3E 00`、`22 F1 9E` 候选载荷及 `FC 00 / FD 00` 地址编码。第二次包含接口返回，未取得 ECU 有效回应。新增纯离线提取器，3 项针对性测试通过。收尾发生涉及 Frida 的安卓系统进程崩溃及存储暂不可用，采集工具稳定性待修复，不能直接用于实车采集。见 [采集记录与证据等级](research/can-code/x431-dme-bench-2026-09-13.md)。vLinker 实车通信、CAN 地址/速率标定及设码仍未验证；不计入实车功能完成。

### 2026-09-13 OBD 离线第一版

已新增可操作的模拟采集、标准响应解析、六项实时值与趋势、故障码/冻结帧、限时自动收尾、原始证据保存、历史回放和 JSON 导出。数据通过独立 OBD 进程采集、原有 DB 进程确认落盘，模拟来源与手工实车档案隔离。正常、无码、无响应、掉线、不完整数据、存储失败和进程中断已覆盖。操作方法和未交付项见 [离线演练说明](obd-offline-guide.md)。

验证：`npm test`（95 项）、`npm run accept:all`（12/12），生产构建通过；新 OBD 页面独立严格类型检查通过。Electron 界面测试覆盖模拟采集、趋势、停止保存、冻结帧、导出、退出重开与回放。完整桌面 TypeScript 检查仍受原有 `LocatorGlbViewer.tsx` 的 three 类型/空值问题影响，未计为通过。

回归发现原 `locator-accept` 会写日常库；本次产生的测试记录已精确删除，并将该测试改用 `locator-accept.db`，独立复验通过。实车 vLinker 接入、981 扩展诊断、X431/Panda 抓包和设码均未实现。当前无需连接设备。

### 2026-09-12 OBD 架构设计

已完成 [实时 OBD 与独立设码架构](obd-architecture.md) 设计稿：vLinker FS BT 主适配器、MX+ 保留 RaceChrono、独立通信进程、模拟/回放、SQLite 数据与逐项设码状态机。当时设备全部未连接，尚无新增运行时代码；A–C 基础闭环已于 2026-09-13 实现，见上方最新进展。

2026-09-13 补充：现场环境要求尽量缩短接车时间。架构 §13 增加离线就绪门槛、限时基础采集计划、自动收尾/保存与最小补采机制，先备齐软件再安排实车窗口。

---

## 1. 已交付（工程可复验）

### 1.1 核心业务

| 能力 | 状态 | 验收 / 入口 |
|------|------|-------------|
| 车辆档案 + 里程只增 | 已定 | `accept:mileage` |
| 保养间隔种子 16 条全部 audited；软提醒 6 条 | 已定 | `accept:interval`；`sync:intervals` |
| 无更换史 → `no_baseline`（不瞎算） | 已定 | `accept:interval` |
| 零件 CSV 导入（按 sku upsert） | 已定 | `ingest:petka`；zone CSV 本机 gitignore |
| Design911 公开 GBP 快照 +「待 PETKA」徽章 | 已定 | Catalog / Locator；`price-verify` |
| **teile.com OEM EUR 快照（7 SKU）** | 已定 | `.local/teile-prices.json`；CSV notes 标 URL；**不**标 PETKA 已核 |
| **展示一律折算 CNY**（手工汇率，入库仍源币种） | 已定 | `data/seed/fx.json`（含 EUR **7.9**）；`formatMoneyAsCny` |
| `fuel-filter` / `tire-fl` 价空 + App 禁填燃油滤价 | 已定 | domain + Catalog |
| P-Loc-1 三区占位 + 热点 + 零件卡更换史 | 已定 | `accept:locator`（真 overview 可后续补） |
| **X-ray 拼装 + 单模型**（CMS 引擎/底盘 + flat-six 幽灵壳） | 已定 | Locator 默认 X-ray；mesh↔热点 → `.local/cms-mesh-map.json` |
| **flat-six 座舱缓存**（981 主用 + 982 保留筛选） | 已定 | `fetch:flat-six-cabins`；`data/seed/flat-six/` |
| **flat-six.org/garage 外观全量**（987/981/982/991 独立 GLB） | 已定 | `fetch:flat-six-garage`；[`garage-models.json`](../data/seed/flat-six/garage-models.json)；Locator `fs-*` browseOnly |
| **981 底盘/排气 PETKA 图号分件**（Tripo） | 已定 | [`chassis-exhaust.json`](../data/seed/petka-models/chassis-exhaust.json)；`.local/petka-models/`；X-ray 微调 / Locator / 车库透视已换 19 件（**引擎+燃油** 7 · **传动+前后轮** 12）；车壳仍 flat-six；整体缩放 |
| **内饰筛选 + 手拼种子（draft）** | 已定 | 主用 `cabin981`；[`interior-stitch.plan.json`](../data/seed/flat-six/interior-stitch.plan.json)；`node data/seed/flat-six/interior-stitch.selfcheck.mjs`；不挡 P-Loc-1 |
| **P-Loc-2 interior / front-trunk 草稿** | 已定 | `zones.json` 含 `interior`（`cabin981`）+ `front-trunk`（`body`）；`apply:interior-stitch`；不挡 `accept:locator` |
| **P-Loc-2 electronics / fluids 草稿导航** | 已定 | `zones.json` 含 `electronics`（`body`）+ `fluids`（`engine`）；[`systems-draft.plan.json`](../data/seed/locator/systems-draft.plan.json)；零件仍挂 P-Loc-1；不做全量 3D |
| **P-Loc-2 导航闭环（非全量 3D）** | 已定 | Catalog `onLocate`；fluids `bridgeJumps`→P-Loc-1；engine-bay `eng-intake`/`eng-block`/`eng-exhaust`；`accept:ploc2`；下一步仅有资产后全量 3D |
| **3D 核对 Phase 1（口径 A）** | 已定 | `status:mesh-map` → `.local/mesh-map-status.json`；姿态/示意闸门 + 热点够用；`eng-*` 纳入；不做 OEM 抽检 |
| **cabin-filter / battery / wiper 上图** | 已定 | `int-cabin-filter`；`front-trunk`；`wipers`（均无独立 mesh；battery 不写 proposedLinks）；真库用 `sync:parts-bootstrap` |
| 故障长期跟踪 `fault_logs` 建/关；知识→定位；关联设码快照 | 已定 | `accept:fault` |
| X431 设码剧本 + after-only 快照（不写 ECU） | 已定 | `accept:coding`；ADR 001；**挂在实时 OBD 内**（非一级 Tab） |
| **can code 并入本仓（2026-08-02）** | 已定 | 研究笔记 `docs/research/can-code/`；明文源 `data/seed/x431/plaintext-archive/`（含 Cayman/982）；`ingest:x431` 默认读本仓 |
| 线束框架（索引 + 打开本地 `data/981.pdf`） | 已定 | `accept:wiring`；路径见 `data/seed/wiring/index.json`（相对仓库根 `data/981.pdf`）；UI 并入零件浏览器 |
| **一级菜单 IA** | 已定 | 零件浏览器 / 维护状态 / **实时 OBD（含设码）** / 车辆设置 / 部件定位；侧栏品牌：`porsche-wordmark.svg` + `porsche-981.svg`（Porsche Next 描边）；OBD Phase 1 → `docs/obd-plan.md` |
| **PETKA 981/982 全量管线（增强，2026-08-02）** | 进行中 | PETKA 在独立主机；另：**teile.com 公开目录已入库**（981 **2176** / 982 **2031** 条 OEM+EUR，标非PETKA）；Catalog 世代筛选；Design911 node 抓取 403，副厂 enrichment 待浏览器续 |
| **OBD Phase 1 码库查码 + 手工会话** | 已定 | `accept:obd-phase1`；`dtc_kb` 种子 **49** 条（发动机/制动/HVAC/车身舒适）；`ObdPage` 查码 + 会话展开 + 记入故障台账；`seedDtcMissing`；无适配器 |
| **手册 OBD/故障中文归档** | 已定 | [`data/seed/dtc/manuals/`](../data/seed/dtc/manuals/README.md)；按 981/982/991；英意核对后只留中文；**不**自动并入 bootstrap |
| **手册错误码逐条核对（分车型并行）** | 已定 | [`by-code/`](../data/seed/dtc/manuals/by-code/)：981=504 · 982=813 · 991=21 → 唯一 **1112**；跨车型同码 222；≠ bootstrap |
| 线束索引 → Locator 草稿区 | 已定 | `locatorZoneId` 最小集；`WiringPage`/`onLocate`；不做 PDF 拆页 |
| Garage 更换备注、软提醒灰 pill | 已定 | Garage UI |
| **车辆设置落盘（漆/内饰/篷）** | 已定 | `vehicle` 表四列 + 车辆设置页保存；X-ray 外板/软顶/内饰跟色已接（`paint-palette.json`）；选色自动填 L0xx OEM 码（20/25） |
| **车库 3D 分栏点穿点选** | 已定 | [`garage-3d-zone-pick.md`](./garage-3d-zone-pick.md)；`LocatorGlbViewer` `pickIgnore`+空 raycast；内饰含「全部」不可点顶篷 |
| **零件浏览器 SYSTEMS 筛选** | 已定 | flat-six id→中文 `system`；`CatalogPage.systemFilter`；芯片计数来自 `listParts`；含 BRAKES/TRANSMISSION/HVAC（制动 5 / 传动 2 / 空调 1） |

### 1.2 后台管线（不抢前台）

| 管线 | 命令 | 说明 |
|------|------|------|
| 手填价 TSV/JSON | `apply\|watch:petka-handcopy` | 模板 `data/petka/_template/prices.tsv` |
| 复制文本 inbox | `ingest\|watch:petka-inbox` | `.local/petka-inbox/*.txt` |
| 定位底图 inbox | `ingest\|watch:locator-inbox` | `.local/locator-inbox/` → `shots/overview.*` |
| 三区 shots 监听 | `apply\|watch:locator-shots` | 已落盘 overview 后验收 |
| 只读现状 | `status:petka` | Etka7 / TSV / CSV 复核计数 / inbox / overview / watch |
| 全量解析 | `parse:petka-bulk` / `:selfcheck` | `.local/petka-bulk/{981\|982}/*.txt` → `parts.csv` |
| 远程同步 | `sync:petka-bulk-remote` | 从 PETKA 主机 drop 拉 txt（`PORSCHE981_PETKA_SSH`） |
| 扫组进度 | `sweep:petka-bulk` | 可中断续跑清单 → `sweep-state.json` |
| 3D 核对（姿态 A） | `status:mesh-map` | GLB/transforms/assemblies + hotspot onMap + eng-* mesh links → `.local/mesh-map-status.json` |
| 打开投喂目录 | `open:petka-inbox` | 价 + 图两个文件夹 |
| 一键四路 watch | `watch:all-inbox` | 按需开，不要求开机常驻 |
| CMS2021 991 引擎+底盘抠模（本机） | `export:cms-rip` / `status:cms-rip` | App 只用 **engine + chassis**；**991 车身不进产品**（ADR 004） |
| flat-six 座舱/车身下载 | `fetch:flat-six-cabins` | → `.local/flat-six/`；见 `data/seed/flat-six/` |
| bootstrap → 真库 upsert | `sync:parts-bootstrap` | 改热点/零件后显式同步；不改 `seedIfEmpty` |

落非空 `oem_price` 且走 handcopy/inbox **PETKA 路径**时：`markPetkaPriceVerified` 清除 pending。  
**teile.com / Design911 公开价**：写入 CSV 但 notes 标「非PETKA价」→ `petka_verified` 保持 0；UI 仍显示「待 PETKA」（未 OEM 核号）。

### 1.3 本机数据快照（2026-07-31 晚；3D 口径 2026-08-01 更新）

| 项 | 现状 |
|----|------|
| CSV 有 OEM 价 | **12/17**（含 teile EUR 7 条 + 既有 Design911 等） |
| PETKA 已核 | **0/17**（抄价走 teile；徽章保留表示未核号） |
| 强制价空 | `fuel-filter`、`tire-fl` |
| 定位 overview | **3/3** P-Loc-1（Design911 981 爆炸图 → `data/petka/*/shots/overview.jpg`；见 `.local/design911-overview-manifest.json`） |
| CMS GLB（App） | engine / chassis **就绪**；**991 body 不进 App** |
| flat-six | 981 Boxster 车身/座舱 + 982 保留套（筛选用） |

**teile.com OEM（EUR，非 PETKA 核）**

| sku | EUR |
|-----|-----|
| oil-filter | 28.61 |
| air-filter | 54.61（只，×2） |
| spark-plugs | 30.06 |
| coil-pack | 119.61（只，×6） |
| front-brake-pads | 245.41 |
| rear-brake-pads | 193.48 |
| pdk-fluid | 23.03 |

副厂价仍以 Design911 GBP `aftermarket_price` 为主；多品牌槽 `parts.aftermarket_quotes`（Catalog 可编辑，Locator 展示主价+多品牌摘要；`ingest:petka` 可选列 `aftermarket_quotes` 紧凑格式 `Brand:price|…`）。`serpentine-belt` OEM 仍 Design911 GBP（teile 无合适标价）。

### 1.4 质量与运行时

| 项 | 状态 |
|----|------|
| `accept:all`（domain+db 各 build 一次） | **11** suite（core 8 + extra 3：`p0`/`p123`/`bridge`） |
| domain / db vitest | `npm test` |
| db-bridge 崩溃限次自愈 + UI 条 | `accept:bridge` |
| xray / cms-assets selfcheck | `node apps/desktop/electron/*-assets.selfcheck.mjs` |

---

## 2. 硬边界（不变）

- 不写 ECU / 无云 / Windows 本地 SQLite  
- 不破解、不解析 PETKA `Data1` / `.zgd`；不把 `DATA\PO` 整包进仓库  
- 验收**量化条数先不写**  
- 默认不对 PETKA/Etka7 键鼠注入、激活窗口、杀进程；**仅用户明确允许本会话 GUI 时例外**（ADR 003）  
- **不用 CMS 991 车身**进产品；车身/内饰用 flat-six（CC BY）

---

## 3. 收敛结论

### 3.1 2026-07-31（Q1–Q11）

| # | 结论 |
|---|------|
| **Q1 R3** | **不要求完整**：Design911 / **teile.com** / 现有价可用；PETKA 核价**不挡交付** |
| **Q2 P-Loc-1** | **A**：三区占位 + 热点即业务可用；真 overview 为增强 |
| **Q3 GUI** | **B**：默认被动 inbox；**仅用户明确允许时**才 GUI（ADR 003）。抄价改 teile.com |
| **Q4 PDK 间隔** | **A**：保持社区保守硬周期 + notes 标厂方 |
| **Q5 off-map** | **部分收口**：`cabin-filter`→`interior`；`battery`/`wiper-blades`→`front-trunk`；电子/循环有草稿导航区，零件仍挂 P-Loc-1（非全量 3D） |
| **Q6 parts.csv Git** | **A**：本机权威；仓库仅模板 + bootstrap |
| **Q7 币种** | **一律展示 CNY**：`data/seed/fx.json`（GBP **9.2** / EUR **7.9** / USD **7.2**） |
| **Q8 fuel-filter** | **A**：VIN+PETKA 核 OEM；价空直至确认 |
| **Q9 981.xlsx** | **A**：关闭议题 |
| **Q10 watch** | **A**：按需开；可选 `watch:all-inbox` |
| **Q11 公开价源** | **teile.com 优先抄 OEM**；Design911 可抄副厂；均标非 PETKA 核 |

### 3.2 2026-08-01（文档/3D 收敛）

| # | 结论 |
|---|------|
| **Q-A R3 主路径** | teile OEM + Design911 副厂；PETKA 仅可选核号；§4 降为历史记录 |
| **Q-B 徽章** | **保留「待 PETKA」**（未 OEM 核号） |
| **Q-C P-Loc** | 2D 占位+热点=业务完成；X-ray/CMS/flat-six=增强；缺 GLB 降级 |
| **Q-D 车身** | **不用 CMS 991 车身**；产品车身/幽灵壳 = flat-six 981；内饰 GLB 保留供筛选 |
| **Q-E 982** | **本阶段保留** 982 座舱缓存作对照；内饰已筛，手拼种子 draft；不做产品世代切换 |
| **Q-F 3D 系统树** | 暂定：车身/内饰 / 发动机(进气+本体+排气) / 底盘(悬架·摆臂·刹车·轮毂·轮胎) / 电子机构(弱电·网关三路 CAN·保险盒) / 循环(水·汽油·机油)；后两区已有 P-Loc-2 草稿导航（零件仍挂 P-Loc-1） |

### 3.3 2026-08-08（UI / 车库 3D 收敛）

| # | 结论 |
|---|------|
| **IA** | 一级仅 5 Tab；**设码并入实时 OBD**（非顶栏） |
| **侧栏品牌** | 官方 PORSCHE wordmark + Porsche Next「981」矢量；型号宽 ≈ 字标 1/5、字高对齐 |
| **车辆设置** | 精简色板 UI；漆→3D `solid`/`metallic`/`special` PBR（`resolveBodyPaintPbr`） |
| **零件浏览器** | 无区 chip；模式切换不卸车身/不 refram；透视幽灵壳半透可透点；进页预载 body+mech |
| **Locator** | 去掉页内 XYZ 微调层；变换只读自 scene |
| **手册 OBD 归档** | `data/seed/dtc/manuals/{981,982,991}/obd-faults.md`（中文；≠ bootstrap）；按码汇总见 `by-code/` |
| **手册错误码逐条核对** | 分车型并行 → [`by-code/`](../data/seed/dtc/manuals/by-code/)（唯一 1112；981/982/991 partial 合并） |
| **过程清理** | 字体描边临时目录 / PDS assets tarball 不入库（`.local/`） |

---

## 4. 常用命令速查

```bash
npm test
npm run accept:all
npm run accept:bridge
npm run status:petka
npm run status:cms-rip
npm run status:mesh-map
npm run export:cms-rip
npm run fetch:flat-six-cabins
npm run sync:intervals
npm run sync:parts-bootstrap
npm run ingest:petka
npm run watch:petka-inbox
npm run watch:locator-inbox
npm run watch:all-inbox
npm run open:petka-inbox
npm run dev
```

CMS 抠模：`docs/cms-991-engine-rip.md`（App：engine+chassis；产物仅 `.local/cms-rip/`）  
flat-six：`data/seed/flat-six/README.md` · X-ray：`data/seed/xray/README.md`  
PETKA：`C:\Program Files (x86)\Digital-Eliteboard\PETKA\Program\EtStart.exe`  
数据只读参考：`D:\ProgramData\Digital-Eliteboard\PETKA\DATA\PO\`（不入库）
