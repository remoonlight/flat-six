# 交付进度与收敛质疑（2026-08-01）

> 权威需求：`docs/requirements.md`（v7）  
> PETKA 采集：`data/petka/README.md`  
> ADR：`docs/adr/001-no-ecu-write.md`、`docs/adr/002-local-only-windows.md`、`docs/adr/003-petka-gui-explicit-only.md`、`docs/adr/004-cms-rip-local-only.md`

本文记录**当前工程成果**与已拍板的**收敛结论**。不替代需求正文。

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
