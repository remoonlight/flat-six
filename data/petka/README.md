# PETKA 明文目录与价格导入

本目录保存人工导出的零件明文和可选定位底图。保养 SKU、981／982 全量目录和公开报价分别维护；不解析 PETKA `DATA\PO` 或 `.zgd`。GUI 操作默认禁止，只有当前会话明确授权时才操作，见 [ADR 003](../../docs/adr/003-petka-gui-explicit-only.md)。

## 文件与成果

- [`_template/parts.csv`](_template/parts.csv)：保养零件 CSV 的字段模板。
- `engine-bay/parts.csv`、`brakes/parts.csv`、`chassis/parts.csv`：保养子集、OEM、源币种、报价日期及适用性备注。
- `data/seed/petka/plaintext-archive/`：全量 981 EPC 明文 OEM／中英文名；启动时安全合并，也可运行 `npm run apply:petka-epc`。
- `.local/petka-bulk/{981,982}/`：全量目录的本机处理区，以 generation 筛选，不覆盖保养子集。
- [公开价格目录](../seed/parts/catalog/README.md)：teile.com 的代际价表与精确 OEM 命中快照。Design911 报价保留自身来源，二者均不是 PETKA 已核价。

历史抄价和底图导入已形成 CSV、种子、导入脚本和状态查询成果，不再重复维护逐轮报价表、GUI 操作经过或接棒清单。价格与核实状态以各行数据及来源日期为准。

## CSV 与价格规则

CSV 使用 UTF-8，首行为表头，空字段保持空白，不填字符串 `null`。`sku` 在保养子集中唯一，`zone` 与目录对应，`currency` 保留真实源币种；UI 按 `data/seed/fx.json` 换算 CNY。本机 PETKA 人工价按实际 GBP／VAT 口径记录，不把税或汇率推成已核价格。

`aftermarket_quotes` 可用 `品牌:价格|品牌:价格` 表示多品牌报价，导入器同时推导副厂展示价。OEM 与副厂价分开，OEM 后继、车型、年款、S／GTS／PCCB 等条件保留在备注中；同号或公开标题不代替 VIN／PETKA 终核。

`fuel-filter` 保留 VIN／PETKA 适用性缺口，价格必须为空；轮胎没有单一通用 OEM 时不虚构号码或价格。正式 PETKA 价只能来自明确人工输入的核实数据。公开价、候选和“待复核”备注不得直接改成 PETKA 已核标记。

## 现有导入入口

- 保养 CSV：`npm run ingest:petka`；运行库默认 `.local/garage.db`，可用 `PORSCHE981_DB` 指定本机库。
- 全量目录：`npm run parse:petka-bulk`，再用现有全量 ingest 入口按 981／982 分开导入。
- 明文 EPC：`npm run apply:petka-epc`。
- 状态：`npm run status:petka`，核对 CSV 价格核实状态、inbox、底图和监听器。

导入和启动合并保留已有用户修改、记录与来源币种，不用全量目录替换保养 SKU。

## 人工价格输入

优先把 [`_template/prices.tsv`](_template/prices.tsv)复制到 `.local/petka-prices.tsv` 后填写已核价格；或填写 `.local/petka-handcopy.json` 的 `parts[].oem_price`。燃油滤价格继续留空。运行 `npm run apply:petka-handcopy`，或显式启动 `npm run watch:petka-handcopy`。

已有明文也可放入 `.local/petka-inbox/`，通过 `npm run ingest:petka-inbox` 单次处理，或 `npm run watch:petka-inbox` 监听。`node scripts/ingest-petka-inbox.mjs --dry-run` 只解析；格式见 [`_template/inbox-example.txt`](_template/inbox-example.txt)。处理后归入 `done/`，最近报告为 `.local/petka-inbox-last.json`。

后台只处理仓库文件和明文导入。正式价格应用时统一更新备注中的待核标记，不靠随意追加“PETKA”字样宣称核实。

## 定位底图

每个保养分区可放 `shots/overview.png`，也支持 JPG／JPEG。截图来源和车型适用性保留；缺图继续使用占位，不从图片目录或其他车型推定本车总览。

- 被动 inbox：`.local/locator-inbox/`，按 `engine-bay.png`、`brakes.png`、`chassis.png` 或既有 overview 命名。
- 入口：`npm run ingest:locator-inbox`／`watch:locator-inbox`，或已有底图落盘后运行 `npm run apply:locator-shots`。
- 热点与零件关系：`data/seed/locator/zones.json`；状态：`.local/locator-shots-status.json`。

底图和 PDF 继续本机保存，不整包复制 PETKA Bilder，不解析安装数据。定位素材缺失和 OEM 未核状态仍按真实资料显示。
