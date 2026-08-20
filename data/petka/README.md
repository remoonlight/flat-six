# PETKA 导出物目录

人工从本机 PETKA 采集后，只把**明文导出物**放这里。不要提交 `DATA\PO` 安装包。

## 目录

```text
data/petka/
  _template/parts.csv     # 字段模板（唯一权威）
  engine-bay/             # 发动机舱
  brakes/                 # 制动
  chassis/                # 底盘
```

全量 981 EPC 明文导出（OEM + 中英文名权威）见 `data/seed/petka/plaintext-archive/`（工作副本 `.local/petka-epc/`），入库 `npm run apply:petka-epc`。

每个 zone 目录约定：

- `parts.csv` — 按模板填一行一个零件
- `shots/` — 可选：爆炸图截图 / 打印 PDF（本地用，可不入库 Git）
  - **定位底图约定文件名**：`shots/overview.png`（也认 `.jpg` / `.jpeg`）
  - 放入后重启 App，「部件定位」对应分区会自动换真图；未放则用占位图并标「待 3D」
  - 用户手截 `overview` 落盘后，后台跑 `npm run apply:locator-shots`（或 `npm run watch:locator-shots` 监听三区 `shots/`）自动验收并写 `.local/locator-shots-status.json`；不碰 PETKA GUI
  - **被动底图 inbox（推荐）**：手截后丢进 `.local/locator-inbox/`（文件名见下），后台归位并跑 accept：
    ```bash
    npm run watch:locator-inbox
    npm run ingest:locator-inbox
    node scripts/ingest-locator-inbox.mjs --dry-run
    ```
    约定：`engine-bay.png` / `engine-bay-overview.png` / `engine-bay/overview.png`（brakes、chassis 同理）。处理后进 `done/`，目标 `data/petka/<zone>/shots/overview.<ext>`，报告 `.local/locator-inbox-last.json`。说明：`data/petka/_template/locator-inbox-README.txt`
  - 热点坐标在 `data/seed/locator/zones.json`，`id` 对齐 CSV/`parts.locator_hotspot`

## 采集步骤（需求流程）

1. 打开本机 PETKA（`EtStart.exe`），选 981 / Boxster S 相关目录
2. 在软件里查 OEM / 装配图 / 价格（本机币种 GBP + VAT 20%）
3. 用打印→PDF、截图、或零件表复制文本；**不要**解析 `Data1` / `.zgd`
4. 整理进对应 zone 的 `parts.csv`
5. Design911 复审后填 `applicability_note`，`design911_checked=yes`（或 `partial` / `no`）
6. 导入本 App：`npm run ingest:petka`（按 `sku` upsert 进 `.local/garage.db`；可用 `PORSCHE981_DB=` 指定库）

**可选列 `aftermarket_quotes`**（多副厂品牌，Excel 友好紧凑格式）：`品牌:价格|品牌:价格`，例如 `Design911:14.95|Mann:9.50`。有该列时写入 `parts.aftermarket_quotes`，并自动推导 `aftermarket_price`（优先 Design911，否则取第一个）。旧 zone CSV 无此列仍可导入。

### engine-bay 第一批（2026-07-30）

- 已写入本地 `engine-bay/parts.csv` 并导入（已入库 Git）
- 公开目录复审纠正多条错误 OEM（bootstrap 已同步）

### brakes / chassis 第二批（2026-07-30）

- `brakes/parts.csv`：制动液、前后片、磨损传感器
- `chassis/parts.csv`：PDK 两油、燃油滤（未证实）、前摆臂、轮胎占位
- 前/后刹车片 OEM 已从错误的 `9A1.698.*` 更正；**VIN/PETKA 终核**仍必要（尤其前片 S/GTS 目录表述有冲突）

### 报价快照第三批（2026-07-30）

**策略**：本机 PETKA GUI（`EtStart.exe` 已安装）未能稳定自动化抄价 → 改用 **Design911 公开 GBP 标价**（辅 Ravenol）写入；`notes` / `applicability_note` 标明 URL +「非 PETKA 价，待本机复核」。

| sku | oem_price | aftermarket_price | 来源 |
|-----|-----------|-------------------|------|
| oil-filter | 24.58 | 14.95 (Mann) | Design911 OEM / Mann |
| engine-oil | — | 59.39 (Mobil1 5L) | Design911；OEM瓶待 PETKA |
| air-filter | 50.40/只 | 24.00 OE Match/只 | Design911；需×2 |
| spark-plugs | 27.45/只 | 24.60 Bosch×6 | Design911 |
| serpentine-belt | 69.82 | 29.50 Gates | Design911；后继 0PB.903.137.* |
| coolant | 14.40 (1L) | — | Design911 |
| coil-pack | 120.33/只 | 29.95 Beru | Design911 |
| brake-fluid | 30.82 (1L) | — | Design911 |
| front-brake-pads | 227.18 | 99.00 Brembo | Design911 |
| rear-brake-pads | 175.13 | 55.00 Textar | Design911 |
| front-pad-sensor | 66.78 | 11.95 | Design911 |
| rear-pad-sensor | 47.16 | 15.00 | Design911 |
| pdk-fluid | 23.22 FFL-3 | 21.95 Ravenol | Design911 / Ravenol |
| pdk-gear-oil | — | 19.70 Motul 75W-90 | Design911；OEM公开价稀缺 |
| control-arm-front-rr | — | 135.00 OE Match | Design911 |
| fuel-filter | — | — | 须 VIN；价空 |
| tire-fl | — | — | 无单一 OEM；跳过 |

`currency=GBP`，`price_as_of=2026-07-30`。导入：`npm run ingest:petka`。

### 报价快照第四批（2026-07-31）— teile.com

**策略（用户拍板）**：**抄价不用 PETKA，改 [teile.com](https://teile.com/)**。OEM 价写 EUR；notes 标 URL +「非PETKA价，teile.com 快照，待复核」；**不** `markPetkaPriceVerified`。副厂价继续用 Design911 GBP。快照：`.local/teile-prices.json`。

| sku | oem_price (EUR) | 备注 |
|-----|-----------------|------|
| oil-filter | 28.61 | teile PN 后继可见 |
| air-filter | 54.61 /只 | 车需×2 |
| spark-plugs | 30.06 /只 | |
| coil-pack | 119.61 /只 | ×6 |
| front-brake-pads | 245.41 | |
| rear-brake-pads | 193.48 | 后继号见 notes |
| pdk-fluid | 23.03 | FFL-3 1L |

未改 teile OEM：`serpentine-belt`（仍 Design911 GBP）、`fuel-filter`（价空）、`tire-fl`（跳过）。  
`status:petka`（当时）：`filled=12/17`，`verified=0`，overview `0/3`（后已补 **3/3** Design911 爆炸图 → `data/petka/*/shots/overview.jpg`，见 `.local/design911-overview-manifest.json`）。

### 本机 PETKA 复核报价 / 底图（停用抄价，2026-07-31）

**进展**：曾短暂到 **「PETKA - Porsche」**；FI 能命中 981。用户改口后**停止 PETKA GUI 抄价**，改 teile.com。

**仍可做（非抄价）**：手截三区 overview → locator-inbox；OEM 争议时再开 PETKA 核号（`fuel-filter` 等）。

**代理约束**：默认禁止抢前台（ADR 003）。抄价走公开站；底图仍被动 inbox。

### 被动收价（inbox，可选）

代理**不操作** PETKA GUI。你在 PETKA 里复制零件行 → 粘贴保存为 `.local/petka-inbox/xxx.txt` → 后台自动吃：

```bash
npm run watch:petka-inbox   # 常驻监听
# 或单次：
npm run ingest:petka-inbox
node scripts/ingest-petka-inbox.mjs --dry-run   # 只解析，不改 CSV
```

格式示例见 `data/petka/_template/inbox-example.txt`（sku/OEM + TAB 或空格 + 价）。有价才写入 `.local/petka-prices.tsv` 并调用 `apply:petka-handcopy`；`fuel-filter` 强制不写价。落非空 `oem_price` 时 apply 会从 CSV `notes` 清掉「非PETKA价 / 待本机复核」等 pending 标记并写入正式「PETKA价」（使 `isPetkaPriceVerified` 为真）；不要只 append 无意义的 `PETKA inbox`。处理完移到 `.local/petka-inbox/done/`，报告 `.local/petka-inbox-last.json`。

**请你手操（最短）**——**必须自己用 EtStart 选 Porsche，并保持窗口，勿让代理代重启**：
1. 结束 Etka7 → EtStart → **Porsche** → 标题确认 `PETKA - Porsche`
2. 进 **981 Boxster** → 选年；FI 查 `9A1.107.224.00` / `000.043.203.66` 抄 GBP 价；`9A1.201.511.00` 只核 OEM
3. 回写 `parts.csv`（`notes` 标 PETKA）→ `npm run ingest:petka`
4. 三区爆炸总览各截一张 → 丢进 `.local/locator-inbox/`（或直接 `data/petka/<zone>/shots/overview.png`）→ `npm run ingest:locator-inbox` / `watch:locator-inbox`（或 `apply:locator-shots`）
5. 可选：非管理员开 PETKA，或同完整性开 Cursor，再让代理代点

详情：`.local/petka-handcopy.json`、`.local/locator-shots-status.json`。

#### 最短手抄清单（SKU + OEM）

| sku | OEM | 抄什么 |
|-----|-----|--------|
| oil-filter | 9A1.107.224.00 | OEM 价 |
| engine-oil | 000.043.206.69 | OEM 瓶装价（若有） |
| air-filter | 981.110.130.00 | OEM 价（注意×2） |
| spark-plugs | 999.170.151.90 | OEM 单价 |
| serpentine-belt | 9A1.102.218.00 | OEM 价 |
| coolant | 000.043.301.47 | OEM 价 |
| coil-pack | 9A1.602.104.07 | OEM 单价 |
| brake-fluid | 000.043.203.66 | OEM 价 |
| front-brake-pads | 981.351.939.04 | OEM 价 + 确认 S 非 PCCB |
| rear-brake-pads | 987.352.939.01 | OEM 价 |
| front-pad-sensor | 991.609.161.00 | OEM 价 |
| rear-pad-sensor | 981.609.163.00 | OEM 价 |
| pdk-fluid | 000.043.207.29 | OEM 价 |
| pdk-gear-oil | 999.917.546.00 | OEM 价 |
| control-arm-front-rr | 991.341.053.03 | OEM 价 |
| fuel-filter | 9A1.201.511.00 | **仅核 OEM**；价继续留空 |
| tire-fl | （无） | 跳过 |

### 定位底图（P-Loc-1）

- 种子：`data/seed/locator/zones.json` + `placeholders/*.svg`
- 可选真图：`data/petka/<zone>/shots/overview.png`（gitignore，勿整包 Bilder）
- **接棒说明**：Bilder 有 `P01`… 树，但无稳定「区→图号」映射；勿盲拷。请在 PETKA（Porsche）爆炸图/总览窗口截三张，分别存：
  - `data/petka/engine-bay/shots/overview.png`
  - `data/petka/brakes/shots/overview.png`
  - `data/petka/chassis/shots/overview.png`
  然后重启 App；未放则继续占位 +「待 3D」
- **被动 inbox**：截图丢 `.local/locator-inbox/` → `npm run watch:locator-inbox` / `ingest:locator-inbox`（见 `_template/locator-inbox-README.txt`）
- App：`locator:map` → 分区切换 + 热点筛零件 + 零件卡

---

## CSV 规则

- UTF-8，首行为表头，逗号分隔
- 空字段留空，不要写 `null`
- `currency` 必填（本机 PETKA 价写 `GBP`）
- `sku` 项目内唯一，建议小写短横线（如 `oil-filter`）
- `zone` 与目录名一致：`engine-bay` | `brakes` | `chassis`
- `fuel-filter`：**价必须空**；须 VIN+PETKA；App 内禁填价


用户币种/税（本机 PETKA）：**GBP** + VAT 20% 写入 CSV `currency`；**App UI 一律折算 CNY**（`data/seed/fx.json` 手工汇率，默认 GBP→CNY 9.2）。

## 现状只读

```bash
npm run status:petka   # Etka7 / TSV / csv_price_verify / inbox / overview / watches
```

`csv_price_verify` 用 domain 的 `isPetkaPriceVerified` / `needsPetkaPriceVerify` 扫三区 `parts.csv`（须已 `build` domain）。

## 后台手抄价管线（勿抢前台）

**约束**：代理/脚本只动仓库文件与 `ingest`；**绝不** SendKeys、鼠标、抢焦点、截屏、杀/重启 Etka7/PETKA。

### 最短用法（推荐 TSV）

1. 复制 `data/petka/_template/prices.tsv` → `.local/petka-prices.tsv`（已在 `.local/`，gitignore）
2. 在表格里填 `oem_price`（`fuel-filter` 留空），保存
3. `npm run apply:petka-handcopy` 或开着 `npm run watch:petka-handcopy`（监听 JSON + TSV，防抖）

### 备选 JSON

填 `.local/petka-handcopy.json` 的 `parts[].oem_price`（`fuel-filter` 保持 `null`），同样 apply/watch。

```bash
npm run apply:petka-handcopy
npm run watch:petka-handcopy
```

非空价会写 `data/petka/{zone}/parts.csv` 并 `ingest:petka`；全空则 `updated_count=0`、跳过 ingest。摘要含 `prices_tsv` / `updated_skus` / `ingest_ok`。

