# 2014 Boxster S（981）本地车库 — 业务需求 v7

> 2026-10-03 实时数据：先选择控制单元，再列出该单元可采集项并多选；只采集勾选项，文字与时间曲线可切换或同屏显示。多选预算参考 X431 分图界面采用 12 项，当前执行清单为 DME 六项标准参数；其他单元无已接入项时明确显示，不能借用 DME 或离线 X431 值。拓扑跳转带入模块，采集期间锁定选择，切换选择清空旧显示；模拟、历史及中断来源继续区分。详见 [当前使用](obd-current-guide.md)。

> 2026-10-03 离线准备：完成现有资料支持的身份字段解析、去重参数目录、选参读取分组、历史响应回放和逐单元接车清单，并接入现有实时数据页面。版本候选与来源缺口保留；历史回放显示原始时间、物理值和枚举，不伪造缺失响应。离线计划不会发送车辆请求；独立实时采集须另行完成本车身份、响应与周期验证。

> 2026-10-03 数据列表：按 X431 来源分组分类筛选；参数改为单行选择排列，分类/分页切换保留已选项，列表内部纵向滚动。名称、单位、记录状态同一行显示。

> 2026-10-01 界面梳理：**实时 OBD → 设码与编程 → 系统拓扑清单的系统 → 维护／设码／特殊功能／编程 → 功能详情**。981／982 在同一目录展示，不以车系拆分入口；功能、参数、流程和规则保留车系证据与适用性说明。四类独立显示，无资料显示暂无内容。功能方案、操作记录和原始归档作为辅助入口。整车流程与未纳入拓扑的来源系统单独标明；不将 Tiptronic 菜单推定为 PDK。此次调整不扩展实车执行范围。

> 2026-10-01 PIWIS 范围更新：用户要求将 **981／982 维护与编程**整合到实时 OBD 的同一工作区。交付目录、流程、滚筒保持方式、本机刷写规则预览和离线准备清单；实车执行按 ADR 001 逐功能资格化。**991.1／991.2 仅预留 Markdown 接口**。见 维护与编程（本机资料：`docs/piwis-workshop.md`） 和 991 接口预留（本机资料：`docs/piwis-991-interface.md`）。车辆档案仍默认本车 2014 Boxster S PDK。

> 2026-10-01 适配器范围更新：支持 **OBDLink MX+ 的 Windows 蓝牙 SPP 连接**。按蓝牙 MAC 唯一匹配当前 COM，允许显式选择并保存设备/手动型号，连接后持续读取 ATRV，断开或通信失效清除电压；读诊断时沿用已选设备和既有互斥链路。不能仅凭 MX+ 名称推定 RaceChrono 占用；使用前由用户断开其他应用。连接说明见 obdlink-mxplus.md（本机资料：`docs/obdlink-mxplus.md`）。实车诊断授权与身份资格化边界保持 ADR 001 的规定。

> 2026-09-27 范围更新：用户要求逆向 X431 的故障码、数据定义和隐藏功能，最终由本项目经 vLinker 独立与车通信；优先发动机实时参数与刷隐藏功能。独立设码由永久排除改为逐功能研究/验证目标，当前车辆操作限已定义只读诊断及 ADR 001 的具名 DME/Gateway 受限清码例外；清码与独立设码的验收状态分别判断。以下旧阶段描述与此冲突时，以 [ADR 001](adr/001-no-ecu-write.md) 的当前决定和 [当前进度](progress.md) 的验收范围为准。

> 状态：边界已锁；Q1–Q11（2026-07-31）+ Q-A–Q-F（2026-08-01）已拍板 → 交付历史（本机资料：`docs/archive/progress-through-2026-10-02.md`） §3\
> 仓库根：本机克隆路径（勿把个人绝对路径写回文档）\
> 相关 ADR：`docs/adr/001-no-ecu-write.md`、`docs/adr/002-local-only-windows.md`、`docs/adr/003-petka-gui-explicit-only.md`、`docs/adr/004-cms-rip-local-only.md`\
> 变更（2026-08-01）：R3 主路径改 teile/Design911；P-Loc 2D=业务完成 / 3D 透视为增强；**不用 CMS 991 车身**；flat-six 内饰（含 982）保留供筛选；3D 系统树暂定\
> 变更（2026-08-02）：**一级菜单替换**（见 §0 #24）；实时 OBD 为产品方向 → `docs/obd-plan.md`（本机资料：`docs/obd-plan.md`）；线束并入零件浏览器；设码改名；X-ray 微调挂部件定位\
> 变更（2026-08-08）：设码并入「实时 OBD」（一级不再单独占 Tab）；侧栏品牌矢量字标 +「981」；车库 3D/车辆设置 UI 收敛见 交付历史（本机资料：`docs/archive/progress-through-2026-10-02.md`） §3.3

---

## 0. 已确认决定（含历史沿革）

| # | 决定 |
|---|------|
| 1 | **本机 PETKA 安装/配置研究**已完成（§4 为历史记录）；**非**交付门禁 |
| 2 | 导入交换格式：简单可维护的 **CSV**，权威模板 `data/petka/_template/parts.csv`（说明见 `data/petka/README.md`） |
| 3 | 线束进 App：**只做框架**（系统索引占位 + 外链打开本地 PDF；可跳 Locator 草稿区）；不做拆页热点；**UI 挂在「车库·零件浏览器」内**（2026-08-02） |
| 4 | 验收条数 / 热点下限等**量化标准先不写**；§6 保持业务口径 |
| 5 | **社区间隔草稿**：写入 `data/seed/intervals/community-draft.json`，默认 `audit_status=pending`；审计流程见同目录 README |
| 6 | **无更换史**：不算剩余公里/天，`status=no_baseline`（UI「需登记首次更换」）；**禁止**用假交车日/购车日起算 |
| 7 | **R3 不要求完整**：teile.com / Design911 快照可用交付；PETKA 核价**不挡交付** |
| 8 | **P-Loc-1（2D）**：三区占位 + 热点即业务可用；真 `overview` / 3D 为增强 |
| 9 | **PETKA GUI**：默认被动；抄价主路径仍 teile.com；**历史会话（2026-08-02）曾明示允许**在 PETKA 主机上驱动 GUI / 剪贴板投喂做 981+982 全量目录（仅记录当时 ADR 003 例外，不继承到当前会话；不解析 Data1） |
| 25 | **双世代全量零件（增强）**：`parts.generation`=`981`\|`982`；sku=`{gen}-{oemNormalized}`；不覆盖保养子集 sku；Catalog 世代筛选；远程 drop → `npm run sync:petka-bulk-remote` → `parse:petka-bulk` → `ingest:petka -- --file`；进度 `.local/petka-bulk/sweep-state.json` |
| 10 | **PDK 油液**：硬周期用社区保守；notes 保留厂方对照 |
| 11 | **知识库 off-map**：`cabin-filter`→`interior`；`battery`/`wiper-blades`→`front-trunk`；电子/循环仅 P-Loc-2 草稿导航（零件仍挂 P-Loc-1，不做全量 3D） |
| 12 | **zone `parts.csv`**：本机权威；模板、bootstrap 与已明确可提交的价表保留，按现有跟踪/忽略规则判断；运行数据不自动入库（禁止 DATA\PO） |
| 13 | **币种展示**：**一律折算 CNY**；入库保留源币种 + `currency=`；手工汇率 `data/seed/fx.json`（GBP/EUR/USD） |
| 14 | **`fuel-filter`**：价空；VIN+PETKA 核 OEM 后再议可售形态 |
| 15 | **`981.xlsx`**：**不纳入**（议题关闭） |
| 16 | **watch**：按需开启；不要求开机常驻（可选 `watch:all-inbox`） |
| 17 | **公开价源**：teile.com 抄 OEM；Design911 可抄副厂；均标非 PETKA 核；**零件卡多副厂品牌位**（`aftermarket_quotes` JSON；UI/DB 已预留落地，Catalog 可编辑多品牌） |
| 18 | **「待 PETKA」徽章**：保留（表示未 OEM 核号；teile/Design911 不冒充已核） |
| 19 | **3D 车身**：产品用 **flat-six 981**（幽灵壳/外板）；**不用 CMS 991 车身** |
| 20 | **内饰模型**：已筛；主用 `cabin981`；手拼种子 `interior-stitch.plan.json`；982 仅对照；不做产品内世代切换 |
| 21 | **3D 透视系统树（暂定）**：见 §2 R4.1；工程子集 + P-Loc-2 草稿区（interior / front-trunk / electronics / fluids）；电子/循环全量 3D 另议 |
| 22 | **P-Loc-2 导航闭环**：Catalog/Wiring/故障 → Locator；fluids `bridgeJumps` 打开零件锚点；engine-bay 系统树 `eng-*`；`accept:ploc2`。下一步仅有资产后的电子/循环/发动机分块全量 3D |
| 23 | **3D 核对口径 A（姿态/示意）**：只核对 GLB/transforms/assemblies 就绪与热点上图；**不做 OEM 下单抽检**。映射策略=热点够用（保养件有 hotspot 即可，不要求每个 mesh 绑 sku）。报告写 `.local/mesh-map-status.json`（`npm run status:mesh-map`）。`eng-intake` / `eng-block` / `eng-exhaust` 纳入导航与 mesh-map 核对 |
| 24 | **一级菜单**：①车库·零件浏览器（含线束）②零件维护状态③实时 OBD（查码+会话；**设码子页并入此处**）④车辆设置（漆/内饰/篷；3D 跟色）⑤部件定位（含 X-ray）；侧栏 `porsche-wordmark.svg` + `porsche-981.svg`；详情 [`progress.md`](./progress.md) |

---

## 1. 产品定位

| 项 | 决定 |
|----|------|
| 端 | Windows 本地桌面（Electron） |
| 车 | **单车型号**：2014 Boxster S（981），**PDK** |
| 同步 | 无云、无账号 |
| Coding | 保留 X431 人工剧本；新增隐藏功能定义逆向及独立设码目标。当前没有独立设码写入能力；现场仅限已定义只读诊断及 ADR 001 的具名 DME/Gateway 受限清码例外，清码实车验收待完成；未来自动写入须有原值备份、差异、回读和恢复验证（ADR 001） |
| 间隔 | 社区经验值 **先入库 → 人工后审计再改** |
| 零件主数据 | **公开站手工快照**（teile OEM / Design911 副厂）为主；可选本机 PETKA 核号；缺数可后续补齐 |
| 报价 | 手工快照；**不过期作废**；**UI 一律折算 CNY**（手工汇率，见 `data/seed/fx.json`）；未核号保留「待 PETKA」徽章 |
| 定位首期 | **2D**：发动机舱 / 制动 / 底盘（占位或 PETKA overview + 热点）即业务可用；**3D 透视**为增强（§2 R4） |
| 故障 UI | 故障页列表长期跟踪；**不做首页未关闭看板** |
| PIWIS | 981／982 维护与编程参考目录、本机规则预览及准备清单；具体执行逐功能验证。991.1／991.2 仅接口文档 |
| 接线图 | 本地 PDF 为权威；App 内先框架（§2 R7） |
| 厂商数据 | 只收导出明文/图片；**不**整包入库、**不**解析/破解专有数据目录 |

---

## 2. 功能需求

### R1 车辆与里程

- 档案固定：2014 / Boxster / S / 981 / PDK
- 当前公里（可增可减；写入 `mileage_audit`）
- 可选：日均公里、VIN
- 本机 SQLite 存储

### R2 保养与到期

- 更换记录：日期、当时公里、品牌、费用、备注、关联零件
- 间隔种子：联网社区检索 → **先入库（`data/seed/intervals/`，`audit_status=pending`）** → 人工审计改种子并同步 parts
- 到期 = 时间与里程约束中更紧者；展示剩余公里/天与状态
- **无更换史（无对应 `service_records`）**：不算出剩余公里/天，状态 `no_baseline`；不以交车日/购车日臆造 baseline
- 含 PDK 相关项；不含手动变速箱专项

### R3 零件与报价（teile / Design911；PETKA 可选）

1. **OEM 价**：本机手工从 [teile.com](https://teile.com/) 抄快照 → CSV（notes 标 URL +「非PETKA价」）
2. **副厂价**：Design911 公开快照可抄 → CSV（同上，不冒充 PETKA 已核）
3. 按 `data/petka/_template/parts.csv` 整理后放入 `data/petka/<zone>/`
4. 录入本 App：名称、系统、OEM、间隔、定位分区、OEM 价、副厂价、币种、来源备注、报价日期、适用性备注
5. 报价快照不过期；以最近一次手工快照为准
6. **可选**：本机 PETKA GUI / inbox 核 OEM 号或日后核价（默认不抢前台；ADR 003）；核过后可清「待 PETKA」
7. **不挡交付**：PETKA 核价未齐亦可关 R3 工程口径（见 §0 #7）

### R4 定位（2D 业务 + 3D 增强）

#### R4.0 P-Loc-1（2D，业务完成线）

| 阶段 | 范围 | 表现 |
|------|------|------|
| P-Loc-1 | 发动机舱、制动、底盘 | **占位或 PETKA overview + 可点击热点** 即业务可用；`zones.json` 的 `pending-3d` = **缺真 overview 底图**（与是否有 3D GLB 无关） |
| P-Loc-2 | 内饰 + 前备箱 + 电子/循环（草稿导航） | `zones.json` 含 `interior`（`cabin981`）、`front-trunk`（`body`）、`electronics`（`body`）、`fluids`（`engine`）；缺 overview=`pending-3d`；不挡 P-Loc-1。**零件卡仍挂 P-Loc-1**（`coolant`/`engine-oil`→`engine-bay`，`fuel-filter`→`underbody`，`battery`→`front-trunk`）；electronics/fluids 热点仅系统树占位 |
| P-Loc-2+ | 其余有图部位 | 同策略扩展（未排期）；电子/循环全量 3D 另议 |

- 点选区域/零件 → 打开零件卡（OEM / 报价 / 间隔 / 更换史）
- 实现种子：`data/seed/locator/zones.json`；真底图放 `data/petka/<zone>/shots/overview.png`（未放则占位 +「待 3D」徽章）

#### R4.1 3D 透视（增强；缺 GLB 降级 2D）

有本机 GLB 时 Locator 可开 **X-ray 拼装**（默认）或单模型；mesh↔2D 热点手对写入 `.local/cms-mesh-map.json`。示意 mesh **不得**冒充 981 OEM 零件真相。

**资产策略（已拍板）**

| 层 | 来源 | 说明 |
|----|------|------|
| 引擎 / 底盘·制动示意 | CMS2021 991 抠模（`.local/cms-rip/`） | 仅引擎 + 底盘；ADR 004 |
| 车身幽灵壳 / 外板 | **flat-six 981**（CC BY） | **不用 CMS 991 车身** |
| 内饰 | flat-six 座舱 GLB | **已筛**：主用 981 Boxster（`cabin981`）；Locator `interior` 草稿已接；手拼种子 `interior-stitch.plan.json`；`apply:interior-stitch`；不做 App 内世代切换 |
| 下载 | `npm run fetch:flat-six-cabins` | 见 `data/seed/flat-six/` |

**3D 透视系统树（暂定目标，非首期全实现）**

| 系统 | 子集 |
|------|------|
| 车身 | 外板 / 幽灵壳 |
| 内饰 | 座舱（**已筛**：主用 `cabin-981-boxster` / `cabin981`；Locator `interior` 草稿；手拼可在该区进行） |
| 发动机 | 进气 + 发动机本体 + 排气 |
| 底盘 | 悬挂 / 摆臂 / 刹车 / 轮毂 / 轮胎 |
| 电子机构 | 弱电 / 网关（舒适性 CAN · 驱动 CAN · 碰撞 CAN）/ 保险盒 |
| 循环系统 | 水 / 汽油 / 机油 |

当前工程子集：引擎 + 底盘·制动（CMS）+ 可选幽灵车壳（flat-six）；P-Loc-2 草稿导航含 `interior` / `front-trunk` / `electronics` / `fluids`（后两区无独立 mesh，默认 `body`/`engine` 示意）。**草稿导航 ≠ 零件锚点**：液体/电瓶零件仍挂 P-Loc-1（及 `front-trunk`）；电子/循环全量 3D 另议。

### R5 故障：快定位 + 长期跟踪

- **快**：症状 → 可能系统/部位 → 检查步骤（优先链到三块定位）
- **长期跟踪（默认字段）**：日期、当时公里、症状、部位假设、处理动作、结果、是否关闭；可选关联零件 SKU、设码快照 ID
- 仅故障页列表管理；不要求截图附件
- **2026-08-02**：故障台账 UI 过渡挂在「实时 OBD」下；真 OBD 见 `docs/obd-plan.md`（本机资料：`docs/obd-plan.md`）

### R5.1 实时 OBD（产品方向；Phase 1 已交付）

- **Phase 1**：内置码库查码 + 手工会话落盘（无适配器）；可记入故障台账
- **当前范围**：具名 DME/Gateway 读码与标准发动机六参数已有实现和 CLI 现场记录；新桌面路径的实车验收待完成。其他 ECU 逐项扩展，旧 Phase 描述仅作沿革。
- 当前执行为已定义的**只读诊断**；目标是项目经 vLinker 独立读码和实时读取发动机参数。设码研究与后续逐功能写入验证见 ADR 001。
- 受限清码是唯一已授权的主动例外：仅具名 981 DME/Gateway、目标 ECU 当次单独确认且 X431 已退出后，按身份/DTC/`pre-clear.json`/一次精确请求/复读执行。它不属于只读确认，也不能恢复被清除的 DTC；实车响应尚未观察。
- 发动机标准 Mode 01 六项须先完成具名 DME 身份资格，再固定使用标准 `7DF/7E8` 查询 `0100` 支持位；只读支持位明确支持的项目，不自动回退或重试。它与厂商静态 measurement 分开，后者不进入现场 live 白名单。详细现场步骤见 `vehicle-connection-runbook.md`（本机资料：`docs/vehicle-connection-runbook.md`）。
- 分期与表结构：`docs/obd-plan.md`（本机资料：`docs/obd-plan.md`）

### R5.2 981/982 诊断定义覆盖矩阵

- **目标数据集**覆盖全部 981 与 982 车型/变体的所有控制单元；每个矩阵行记录车型/变体、ECU、DTC、读取定义、设码定义、证据等级及缺失或冲突。该目标数据集不改变本产品当前单车（2014 Boxster S 981 PDK）或现场只读操作边界。
- 目标以外车型不纳入增量数据研究；可保留从其资源结构、加载路径和验证方法得到的可复用逆向发现，但不得把它们导入 981/982 派生定义或当作适用性证据。
- 先用静态定义与已有 981 DME、网关抓包逐项核对。只有请求、响应、ECU 身份和解码规则一致的条目才可标为“捕获一致”；未知或冲突必须保留原状态。
- 其他 ECU 在取得对应抓包或独立 vLinker 会话验证前不得标为“实车验证”。无抓包不等于无定义，也不等于与本车一致。
- 全量覆盖是待完成的矩阵目标；资源包的全量条目、菜单或字符串均不自动成为本车或任一 981/982 变体可执行能力。
- 当前静态范围清单含 403 个变体：排除目标外车型 76 个、未映射目标菜单 36 个、目标 291 个；目标由 12 个明确 981 代号和 279 个共享菜单候选组成。四个车型入口均可到 35 个系统菜单，但共享入口不证明物理装配。静态累计为 DTC 74,865、测量 92,634、设码 59,871、身份 3,920、例程原始记录 5,385；这些是跨变体记录累计，不是唯一功能数、完整车型适配证明或实车验证。

### R5.3 981 诊断与维修流程增强（2026-09-30；2026-10-02 收敛）

2026-10-02 用户确认：保留底层能力和 CLI，清理退役页面及其失效验收；实时 OBD 只保留五个入口，详见 [当前使用说明](obd-current-guide.md)。引导排障与维修对比的记录/数据库能力保留，经本地记录 CLI 操作，不恢复独立页面。以下业务规则仍适用于保留的记录能力。

用户确认借鉴 STEUER14 的功能设计并开始实施；产品仍只面向 2014 Boxster S（981）PDK。公开功能介绍不证明 STEUER14 支持 981，也不构成本项目可使用其协议、设码值或车辆操作的依据。既有 982 对照资料不增加车型切换或本轮实施范围。

| 功能 | 本车目标 | 实施顺序 |
|------|----------|----------|
| 引导式排故 | 故障与控制单元 → 有依据的检查清单 → 参数/人工检查/资料缺口 → 结果记录 → 关联故障台账与零件定位 | 本轮完成本地功能；实车验证另行推进 |
| 维修前后对比 | 同车、同来源的两次诊断记录按控制单元比较新增、仍存在、不再观察到、状态变化及不可比较项；处理备注和结果在本地保存 | 本轮完成本地功能；实车验证另行推进 |
| 隐藏功能配方 | 继续完善现有设码剧本，逐项记录本车硬件、ECU 版本、实际原值、目标差异、备份、回读与恢复条件 | 后续逐功能；不启用未验证写入 |
| 发动机数据记录与分析 | 已有六参数先完成独立实车验证，再增加工况分组、曲线和维修前后参数对照 | 随实际读取证据推进 |
| 控制单元扫描 | 在现有系统拓扑上逐个验证 981 单元；未适配、读取失败与有效无码保持不同状态 | DME/Gateway 后逐项扩展 |
| 保养操作向导 | 关联更换记录、所需零件、检查步骤及仪表保养提示；提示复位需独立验证 | 后续；菜单存在不等于可执行 |

本轮验收规则：

- 引导清单和检查结果由 SQLite 持久化，重启可恢复；区分参考步骤、补充观察和待核实项，不补造阈值或自动给出更换零件结论。
- 引导和对比的独立页面已退役；本地 CLI 与数据库 API 继续保留记录、结果与台账关联，访问记录不连接设备或执行诊断。
- 对比记录保留身份、来源、时间、ECU 和读取完整性。不同车辆、模拟与实车、未知身份/来源及不同 ECU 变体不能混为可比较记录。
- 默认采集没有 VIN 时，可由用户将每条记录明确指派到本机 981；保存指派时间及说明，显示为人工声明，不能冒充实测 VIN。未指派记录不可直接混比，人工声明与实测身份也不能静默合并。
- 仅在前后均有对应单元及故障类别的有效读取时判断“不再观察到”；读取失败、未扫描、未支持或不完整不能算作故障消失。故障消失不等于已修复。
- 对比和处理备注本地保存，可关联故障台账；测试只用隔离数据库及模拟记录，不向生产库预置演示结果。
- 现场权限继续按 ADR 001 和独立诊断实施范围执行。本轮不增加主动设码、执行器、固件或保养复位指令。

功能设计参考：[开发者发布介绍](https://www.taycanforum.com/forum/threads/steuer14-the-future-of-diy-taycan-repairs-is-here.38857/)、[配方及修改明细说明](https://www.taycanforum.com/forum/threads/steuer14-the-future-of-diy-taycan-repairs-is-here.38857/page-7)。本项目上述验收规则为本车实现决策，不是 STEUER14 已支持 981 的声明。

### R6 设码（原 X431 设码补全）

- 2014 设码 / 编程 / 特殊功能目录 + 剧本（种子来自 `data/seed/x431/plaintext-archive` 过滤；研究笔记见 `docs/research/can-code/`）
- before/after 可选
- 当前应用没有隐藏功能写车闭环；后续仅能逐功能加入已提取且完成身份匹配、原值备份、差异、写入/回读与恢复验证的定义。当前候选和缺口见 `hidden-feature-readiness.md`（本机资料：`docs/hidden-feature-readiness.md`）。
- **UI（2026-08-08）**：挂在「实时 OBD」内，不单独占一级 Tab

### R7 线束参考（框架）

- 权威来源：桌面 `接线图\981.pdf`（§5）；App 打开本机 `data/981.pdf`（gitignore，从桌面拷贝）；不做 PIWIS
- App 形态（已定框架）：`data/seed/wiring/index.json` 系统占位列表 + 用系统默认程序打开 PDF
- 系统索引可带 `locatorZoneId` / `locatorHotspotId`，跳到 Locator 对应草稿区（与故障 KB `onLocate` 同模式）；**不做** PDF 拆页/图上热点
- **不做**：PDF 内嵌翻页、图上热点、把 PDF 拷进仓库
- **2026-08-02**：一级菜单不再单独占 Tab；内容并入「车库·零件浏览器」

### R8 车辆设置（档案 + 3D 跟色）

- 参照 flat-six Settings：车漆色板；扩展 **981 真实色码**、内饰、软顶
- 档案驱动部件定位 X-ray 外板/软顶/内饰跟色（`data/seed/vehicle/paint-palette.json`）


## 3. 明确不做

- 当前不执行未经验证的 ECU 写入、隐藏功能修改或固件刷写；隐藏功能定义与独立设码已纳入逐功能研发目标（ADR 001），不把菜单或静态定义视为已验证写车能力。
- 云同步、多用户、多车平台（暂定单车）
- 破解 PETKA / PIWIS、整包安装目录或 `DATA\PO` 进 Git
- 解析专有格式（如 `.zgd`、`Data1` 二进制）做批量抽库
- 实时全网爬价、报价自动过期
- 设码强制 before、截图附件、首页未关闭工单看板
- 首期全车所有系统 3D（含电子机构 / 循环系统全量）
- **PIWIS 实车例程与刷写执行**（离线资料工作区已纳入 R6）
- 线束拆页/热点（框架之后再谈）
- **CMS 991 车身进 App**（产品车身用 flat-six 981）
- App 内 981/982 座舱世代切换 UI（982 仅本机筛选缓存）

---

## 4. 本机 PETKA（历史研究结论，2026-07-30）

> **非交付门禁**（2026-08-01 拍板）。抄价已改 teile.com；本节保留作本机路径与合规边界备忘。可选核号时仍遵守 ADR 003（默认不抢前台）。

### 4.1 来源与本机信息

本机安装目录、数据盘符、注册表、配置及税率记录只保留在本地历史资料。其他电脑不得据旧路径推定安装状态；主机访问使用 `PORSCHE981_PETKA_SSH` 的本机配置。源币种必须在 CSV 中明确填写。

`DATA\PO` 下主要子目录（只读参考，**不入库**）：

- `Bilder` — 爆炸图等（实测 `P01`… 下有 `.tif` / `.png`）；「待 3D」底图可截 GUI 或另存导出图，**不要**把整树拷进仓库
- `Minis` — 缩略 PNG
- `Categories` — `.zgd` 专有格式，**不解析**
- `Data1` / `Data2` — 目录数据（`.ddm` / `.fdt` / `.BIN` 等专有），**不解析**
- `Formular` — 打印表单（与 `PROG1\Formular` 配套：`ET_TEXT` / `ET_BILD` / `ET_BST1` 等）
- `Tnrpics` / `Zubjpeg` — 零件图类资源

### 4.2 可采集形态（对需求的结论）

本机配置与表单表明：**有打印体系，无稳定官方「一键 Export CSV/Excel」可编程接口**；外部绑定未开。

| 形态 | 如何得到 | 本 App 用法 |
|------|----------|-------------|
| **打印 → PDF / 纸质** | PETKA 打印（`[PRINT]` + Formular；`PrintToFile` 默认 `FALSE`，可用虚拟打印机出 PDF） | 存档对照；人工抄 OEM / 价（可选） |
| **截图 PNG/JPG** | 爆炸图 / 零件表窗口截图 | P-Loc-1「待 3D」底图 + 热点标注 |
| **界面复制文本** | 零件表 / 购物清单类界面复制粘贴 | 整理成 `parts.csv` 后放入 zone 目录 |
| **CSV / Excel** | **无**已核实的官方一键导出 | **首选入库交换格式** = 本仓库模板 CSV |

**禁止**：把整个 `PETKA` 安装目录或 `DATA\PO` 提交进仓库；禁止破解式读取 `Data1` / `.zgd`。

### 4.3 采集流程（可选核号时）

```text
本机打开 PETKA GUI（ADR 003：仅明示允许）
  → 打印 PDF 和/或 截图 和/或 复制零件表
  → 人工整理为 data/petka/<zone>/parts.csv（套 _template）
  → 可选 Design911 适用性备注
  → 导入本 App；核号后可清「待 PETKA」
```

首期 zone：`engine-bay` / `brakes` / `chassis`（对应 P-Loc-1）。

### 4.4 与「加密库」措辞的澄清

PETKA 使用**专有/闭源目录格式**（不便当开放 API 读），**不是**本项目已证实的、类似 X431 `YZJM` 那种「标准加密容器 + 必须解密」对象。合规边界是「不整包、不破解」，采集靠 GUI 导出/复制/截图。

---

## 5. 本地参考资料（非 PETKA）

### 接线图（PIWIS 不进；以此为准）

路径：本机线束 PDF 目录（桌面 `981\接线图\`；勿把绝对用户路径提交进仓）

| 文件 | 用途 |
|------|------|
| `981.pdf` | **主车**线束参考（桌面原件；App 打开 `data/981.pdf` 拷贝，见 `data/seed/wiring/index.json`） |
| `982.pdf` | 对照（勿与 981 混用） |
| `981GT4_CS.pdf` | 对照（勿与 981 混用） |

索引种子：`data/seed/wiring/index.json`

### 车主手册（按章节按需摘）

- 本机车主手册 PDF：`Boxster BoxsterS BoxsterGTS (981).pdf`
- 对照：`Porsche 718 Boxster BoxsterS BoxsterGTS (982).pdf`

### 设码种子

- 明文源：`data/seed/x431/plaintext-archive/`（含 Boxster981 / Cayman981 / Boxster982 等；VIN/设备序列号已脱敏）
- App 菜单：`data/seed/x431/981-2014-coding-menu.json`（`npm run ingest:x431` 自 `03-sim-981/981_Boxster981_rows.json` 过滤年款 2014 + 设码/编程/特殊功能）
- 研究笔记：`docs/research/can-code/`

### 其它桌面表

- 本机 `981.xlsx`：存在，**不纳入**导入范围（议题已关闭）

### 3D 资产种子（本机二进制 gitignore）

| 路径 | 用途 |
|------|------|
| `data/seed/xray/assemblies.json` | X-ray 装配表 |
| `data/seed/flat-six/` | flat-six 车身/座舱清单与下载说明 |
| `data/seed/cms-rip/` | CMS 引擎/底盘抠模清单（**不含产品车身**） |
| `.local/cms-rip/` · `.local/flat-six/` | 本机 GLB |

---

## 6. 验收口径（业务）

> 量化阈值（条数、热点下限等）**先不写**。

1. 社区间隔可先导入再审计；登记更换后能算剩余公里/建议日期
2. teile / Design911（或其它手工）报价快照能保存为不过期快照；未核号显示「待 PETKA」；PETKA 核齐**不**作为交付门禁
3. 发动机舱 / 制动 / 底盘：占位或 PETKA overview 可点到零件；有本机 GLB 时可 X-ray/单模型增强，缺则降级 2D
4. 故障页可建/关长期跟踪记录
5. X431「外部放大器 → 设码」类剧本可打开，并可选择存 after
6. 线束页能打开索引，并能用系统程序打开 `981.pdf`（框架）

---

## 7. 仍待收敛 / 下一步

> Q1–Q11 与本轮 Q-A–Q-F 已拍板，业务口径无未决项。

运维：改汇率编辑 `data/seed/fx.json` 后重启 App；改 `bootstrap.json` 热点/零件后跑 `npm run sync:parts-bootstrap`（非空库 `seedIfEmpty` 不补种）；价/底图继续被动 inbox。

**P-Loc-2 导航闭环已完成**。3D 核对 Phase 1（口径 A）：`npm run status:mesh-map` → 手调 `.local/xray-transforms.json` → Locator 单模型 engine 把 mesh 挂到 `eng-*`（姿态/示意，不做 OEM 抽检）。另：有资产后再做电子/循环全量 3D；interior 可按 [`interior-stitch.plan.json`](../data/seed/flat-six/interior-stitch.plan.json) 手对（`npm run apply:interior-stitch`；不挡 P-Loc-1）。

---

## 8. 数据流总览

```text
间隔：社区草稿 → data/seed/intervals（pending|audited）→ 人工审计 → sync:intervals → parts；无更换史 = no_baseline
OEM/价：teile.com（OEM）/ Design911（副厂）手工快照 → CSV → App（入库源币种）
         → UI 经 data/seed/fx.json 折算 CNY；未核号「待 PETKA」（公开价不冒充已核）
         → 可选：PETKA GUI / inbox 核号（默认不抢前台）
定位 2D：PETKA overview / inbox → 未放则占位 + zones pending-3d
定位 3D：CMS 引擎+底盘 + flat-six 车身/座舱 → X-ray（data/seed/xray）→ 缺 GLB 降级 2D
         → 不用 CMS 991 车身；982 座舱仅本机筛选
线束：桌面原件 → 本机 data/981.pdf（App 外链；gitignore；非 PIWIS）
设码菜单：`data/seed/x431` plaintext-archive 过滤种子
现状：npm run status:petka / status:cms-rip / status:mesh-map
```
