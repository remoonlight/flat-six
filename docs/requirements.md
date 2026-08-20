# 2014 Boxster S（981）本地车库 — 业务需求 v7

> 状态：边界已锁；Q1–Q11（2026-07-31）+ Q-A–Q-F（2026-08-01）已拍板 → [`docs/progress.md`](./progress.md) §3  
> 仓库根：本机克隆路径（勿把个人绝对路径写回文档）  
> 相关 ADR：`docs/adr/001-no-ecu-write.md`、`docs/adr/002-local-only-windows.md`、`docs/adr/003-petka-gui-explicit-only.md`、`docs/adr/004-cms-rip-local-only.md`  
> 变更（2026-08-01）：R3 主路径改 teile/Design911；P-Loc 2D=业务完成 / 3D 透视为增强；**不用 CMS 991 车身**；flat-six 内饰（含 982）保留供筛选；3D 系统树暂定  
> 变更（2026-08-02）：**一级菜单替换**（见 §0 #24）；实时 OBD 为产品方向 → [`docs/obd-plan.md`](./obd-plan.md)；线束并入零件浏览器；设码改名；X-ray 微调挂部件定位  
> 变更（2026-08-08）：设码并入「实时 OBD」（一级不再单独占 Tab）；侧栏品牌矢量字标 +「981」；车库 3D/车辆设置 UI 收敛见 [`progress.md`](./progress.md) §3.3

---

## 0. 已拍板（本轮）

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
| 9 | **PETKA GUI**：默认被动；抄价主路径仍 teile.com；**本会话（2026-08-02）明示允许**在 PETKA 主机上驱动 GUI / 剪贴板投喂做 981+982 全量目录（ADR 003 例外；不解析 Data1） |
| 25 | **双世代全量零件（增强）**：`parts.generation`=`981`\|`982`；sku=`{gen}-{oemNormalized}`；不覆盖保养子集 sku；Catalog 世代筛选；远程 drop → `npm run sync:petka-bulk-remote` → `parse:petka-bulk` → `ingest:petka -- --file`；进度 `.local/petka-bulk/sweep-state.json` |
| 10 | **PDK 油液**：硬周期用社区保守；notes 保留厂方对照 |
| 11 | **知识库 off-map**：`cabin-filter`→`interior`；`battery`/`wiper-blades`→`front-trunk`；电子/循环仅 P-Loc-2 草稿导航（零件仍挂 P-Loc-1，不做全量 3D） |
| 12 | **zone `parts.csv`**：本机权威；Git 只留模板 + bootstrap（禁止 DATA\PO） |
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
| Coding | **X431 补全**（剧本 + before/after 可选）；**不写 ECU**；无强制 before；无截图附件 |
| 间隔 | 社区经验值 **先入库 → 人工后审计再改** |
| 零件主数据 | **公开站手工快照**（teile OEM / Design911 副厂）为主；可选本机 PETKA 核号；缺数可后续补齐 |
| 报价 | 手工快照；**不过期作废**；**UI 一律折算 CNY**（手工汇率，见 `data/seed/fx.json`）；未核号保留「待 PETKA」徽章 |
| 定位首期 | **2D**：发动机舱 / 制动 / 底盘（占位或 PETKA overview + 热点）即业务可用；**3D 透视**为增强（§2 R4） |
| 故障 UI | 故障页列表长期跟踪；**不做首页未关闭看板** |
| PIWIS | **完全不进范围** |
| 接线图 | 本地 PDF 为权威；App 内先框架（§2 R7） |
| 厂商数据 | 只收导出明文/图片；**不**整包入库、**不**解析/破解专有数据目录 |

---

## 2. 功能需求

### R1 车辆与里程

- 档案固定：2014 / Boxster / S / 981 / PDK
- 当前公里（只增不减）
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
- **2026-08-02**：故障台账 UI 过渡挂在「实时 OBD」下；真 OBD 见 [`docs/obd-plan.md`](./obd-plan.md)

### R5.1 实时 OBD（产品方向；Phase 1 已交付）

- **Phase 1**：内置码库查码 + 手工会话落盘（无适配器）；可记入故障台账
- **Phase 2+**：硬件读码、实时 PID、AI 方案（见 plan）
- **只读**；不写 ECU（ADR 001）
- 分期与表结构：[`docs/obd-plan.md`](./obd-plan.md)

### R6 设码（原 X431 设码补全）

- 2014 设码 / 编程 / 特殊功能目录 + 剧本（种子来自 `data/seed/x431/plaintext-archive` 过滤；研究笔记见 `docs/research/can-code/`）
- before/after 可选
- 本应用无任何写车能力（ADR 001）
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

- **写** ECU / 写隐藏功能 / 刷写（ADR 001）；OBD **只读**（见 `docs/obd-plan.md`）
- 云同步、多用户、多车平台（暂定单车）
- 破解 PETKA / PIWIS、整包安装目录或 `DATA\PO` 进 Git
- 解析专有格式（如 `.zgd`、`Data1` 二进制）做批量抽库
- 实时全网爬价、报价自动过期
- 设码强制 before、截图附件、首页未关闭工单看板
- 首期全车所有系统 3D（含电子机构 / 循环系统全量）
- **接入 PIWIS**
- 线束拆页/热点（框架之后再谈）
- **CMS 991 车身进 App**（产品车身用 flat-six 981）
- App 内 981/982 座舱世代切换 UI（982 仅本机筛选缓存）

---

## 4. 本机 PETKA（历史研究结论，2026-07-30）

> **非交付门禁**（2026-08-01 拍板）。抄价已改 teile.com；本节保留作本机路径与合规边界备忘。可选核号时仍遵守 ADR 003（默认不抢前台）。

### 4.1 安装与路径

| 项 | 本机值 |
|----|--------|
| 产品 | Lexcom **ETKA / PETKA 8.8**（Digital-Eliteboard 发行） |
| 启动 | `C:\Program Files (x86)\Digital-Eliteboard\PETKA\Program\EtStart.exe` |
| 程序目录 | `C:\Program Files (x86)\Digital-Eliteboard\PETKA` |
| 保时捷数据 `DataPath_PO` | `D:\ProgramData\Digital-Eliteboard\PETKA` → `DATA\PO\` |
| 用户数据 `UserDataPath_PO` | `C:\ProgramData\Digital-Eliteboard\PETKA` |
| 本地配置 | `%LOCALAPPDATA%\Digital-Eliteboard\PETKA` |
| 注册表 | `HKLM\SOFTWARE\WOW6432Node\Lexcom\ETKA`（另有 `PET2` 键） |
| 品牌标记 | `MARKE=PO`（`ETKA_PO.INI`） |
| 用户币种/税（`etka_user_po.ini`） | **GBP**，VAT **20%**（录入本 App 时 CSV 的 `currency` 必填） |
| DMS / 外部绑定（`[ANBINDUNGEN]`） | `Active=0`（本机未启用可程序化对接） |

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

**禁止**：把整个 `PETKA` 安装目录或 `D:\ProgramData\...\DATA\PO` 提交进仓库；禁止破解式读取 `Data1` / `.zgd`。

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
