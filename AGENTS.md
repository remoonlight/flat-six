# porsche981 — Agent map

Windows 本地 Electron 车库：**2014 Boxster S（981）PDK**。无云、无账号。文档入口：`docs/README.md`；权威需求：`docs/requirements.md`；进度：`docs/progress.md`；OBD 当前结果：`OBD_STATUS.md`。

Codex 直接执行；禁止 Cursor CLI / Grok；仅用户为当前任务明确要求委派时使用允许的 Codex worker。历史工作单与迁入记录不构成授权。`docs/` 核心文档以 `.gitignore` 白名单纳入版本管理，设备记录与私人历史继续本地保存；文件归属与未闭合问题见 `docs/convergence-review.md`。

## Packages

| Path | Role |
|------|------|
| `apps/desktop` | Electron UI + `electron/` main/preload/IPC |
| `packages/domain` | 纯业务逻辑（CSV、间隔、货币等） |
| `packages/db` | SQLite（`node:sqlite`）schema / 查询 |
| `packages/obd`、`scripts/diagnostics/` | OBD 定义、传输、离线研究与诊断 CLI |
| `data/seed/` | 可提交种子；含 `x431/plaintext-archive/`（设码明文源）、`petka/plaintext-archive/`（EPC OEM/中英文名）；`data/petka/*/parts.csv` 价表可提交 |
| `docs/research/can-code/` | CAN/X431 研究笔记 |
| `.local/` | 运行时库、inbox、bulk、研究原件与 CMS 缓存不提交；已有 flat-six/PETKA 模型白名单例外以 `.gitignore` 为准，不扩展到 CMS |

## 常用命令

- `npm run dev` · `npm test` · `npm run accept:all`
- macOS 开发启动：`bash scripts/run-desktop.sh`（或根目录 `开始车库.command` / `START.command`）
- PETKA：`status:petka` · `ingest:petka` · `parse:petka-bulk` · `sync:petka-bulk-remote`
- 3D：`status:mesh-map` · `status:cms-rip` · `accept:ploc2` · `archive:petka-models`

## 硬边界（详见 ADR）

1. **诊断与独立设码按阶段实施**：2026-09-27 用户明确要求逆向故障码、数据定义和隐藏功能，最终由项目经 vLinker 独立通信。允许离线协议/设码定义研究；当前可执行范围为已定义的只读诊断与具名 981 DME/Gateway 的受限清故障码，vLinker CLI 已有具名只读与标准发动机实车记录；新桌面整合路径及受限清码实车验收仍待完成。设码及其他写入逐功能建立身份匹配、原值备份、写入/回读与恢复方案后单独验证；不得猜测或重放未知写指令 — `docs/adr/001-no-ecu-write.md`
2. **产品仅 Windows + SQLite**；macOS 仅开发运行 — `docs/adr/002-local-only-windows.md`
3. **PETKA GUI 默认禁止**；仅当用户本会话明示授权 — `docs/adr/003-petka-gui-explicit-only.md`
4. **CMS 抠模仅本机**；产品不用 991 车身 — `docs/adr/004-cms-rip-local-only.md`
5. **禁止**解析/入库 PETKA `DATA\PO` / `.zgd`；只收明文导出/剪贴板/inbox
6. UI 价 **一律折算 CNY**（`data/seed/fx.json`）；teile/Design911 ≠ PETKA 已核

## 数据事实（易错）

- 保养子集（~20 sku）与 **981/982 全量目录**是两套数据；全量走 `.local/petka-bulk/` + `generation` 筛选
- PETKA 主机用本机 SSH / `PORSCHE981_PETKA_SSH`（MCP `ssh-mcp-petka`）；本机未必装 EtStart
- DB 开发路径：`.local/garage.db`；改 schema 走 `packages/db`，勿用 MCP 乱写生产语义数据

## 实现约束

- 2026-10-04 最新九项决定：诊断支持目标为 981＋982 所有控制单元、X431＋PIWIS 功能；持续读取的单次所选参数最新暂定最多 12 项，完整目录不缩减；实际 X431 上限/周期由 Codex 核实，不把暂定值当成已证实上限。普通车辆结果默认不保存，点击保存保留本次采集全过程，换选项丢弃上一批；清码前证据及未来设码完整原值是强制持久备份例外，备份失败不修改。车辆值按核实定义显示，原始报文可选保存为 Wireshark 捕获文件。明确单元失败记录后继续其他合格目标，掉线、身份不符、未知结果或清理失败停止批次；写请求不自动重放。先验收功能与 ECU 层请求/响应及解码一致；新机/迁移须支持项目展示的协议、实时数据、固件和诊断头资料及能力。X431 隐藏设码全部保留为目标，DME 燃油市场为具名候选，先读取并保存完整原码、分析含义、修改、写入并回读核对，不默认美国→欧洲。 标准六项已支持持续采集与显式完整保存，离线计划不算厂商 live 目标完成；需求与最新确认与实现/验收质疑见 `docs/requirements.md` R5.1/R5.2/R6、`docs/convergence-review.md`。不据此删除历史证据、猜测协议或扩大实际写车权限。

- 最新四项回答已收敛：原始报文记录默认关闭，显式“开始记录/结束记录”后流式保存 PCAPNG，只覆盖开启期间实际收到的内容；车辆数值仍按本次采集全过程显式保存。固件操作目标明确为车辆控制单元固件刷写，本轮未新增诊断头升级目标。设码支持完整 X431 范围，流程为读取并持久保存车辆原始码值→分析版本/字段含义→修改目标码值→写入车辆→重新读取核对，保留恢复方案；DME 燃油市场只是例子，不限制范围或指定首个功能。内网只读、不向车辆 CAN 发送数据，按核实的广播字段显示车辆值；设备本地配置与车辆 CAN 发送分开验收。 软件已有备份/预览/演练及固件准备；尚无实际车辆设码/刷写闭环。

- 2026-10-05 最新三项确认：首次车辆/控制单元完整原码基线不覆盖，每次写入前另存本次完整原码、身份、版本和时间；读取失败/未读取列缺项，目标完整备份失败不修改。设码按已核实的字段名称、含义和合法选项修改，显示原值、目标值及原始码差异；完整原码可查看，未修改或未知字段原样保留，不开放未知字节任意写入，完整 X431 设码覆盖目标保持。ECU 固件刷写只考虑有明确来源且匹配控制单元身份/版本的原厂文件；第三方、自定义和调校固件仅保留另开议题，不属于当前实施/验收范围，也不自动列为后续必做功能。 此前三问及恢复范围选择均已关闭；恢复只处理当前所选控制单元，剩余为 Codex 实现与验收责任，不能当成功能已完成。

- 2026-10-05 最新四项确认：诊断用途在当前控制单元内单次最多选择 12 个实时参数，作为暂定产品上限，不声称是 X431 已验证上限，不限制完整目录或内网持续接收。增加“恢复原码”按钮作为编码恢复功能目标，首次基线与每次写前完整备份规则保持；用户已确认仅恢复当前所选控制单元，采用它首次完整原码基线，不处理其他控制单元；恢复前另存当前原码并核对身份/版本，写后回读核对，缺少匹配备份或恢复定义时不可执行。ECU 固件刷写仅允许已确认的实际有线诊断连接，禁止蓝牙刷写；有线是必要条件，仍须核实具体诊断头、原厂文件、控制单元身份/版本、流程与恢复能力，不能仅凭接了线就允许刷写。用户尚未验证内网接收/不发送、显示与保存；已有 CLI/回放记录不替代新桌面完整路径验收。 恢复方案和离线演练已有软件实现；实际车辆恢复尚未资格化，没有新实车通过证据。

- Renderer 仅经 `window.porsche981` 调用业务能力，不直接引入 Node/SQLite；新增 IPC 同步 `electron/main.mjs`、`electron/preload.cjs` 与 `src/api.ts`。数据库经 main / db-bridge / `packages/db`。
- 保养 SKU 与全量 `{generation}-{oemNormalized}` 分开；优先已有 ingest/npm 入口，不用全量目录覆盖保养子集。来源币种保留，UI 折算 CNY。
- 缺失 3D 资源使用 placeholder；simulation、注入传输、离线定义及跨车型响应均不能作为本车能力验收。

## 执行与验收

- 本文件是项目规则入口；`.cursor/rules/` 仅兼容旧编辑器，不作为 Codex 唯一规则来源。先看 `git status --short`，再读相关源码、需求和 ADR；历史报告仅按需要定位，不整批重读归档。
- 搜索先限定目录、文件类型和问题：`rg -n` / `rg --files`。默认不遍历 `.local/`、归档、生成物或二进制；研究任务指定子目录再读。大表先看列、数量和少量样本；GLB 只取大小/header，截图验证显示。
- 独立只读检查可批量并行；修改、构建产物、共享数据库、Electron、转换与设备会话串行。Codex 直接执行，委派仍需当前任务明确授权。
- 长任务使用可轮询进程和显式总超时；轮询只收新输出，间隔随无变化增长。只结束本任务启动且身份已核对的进程，不杀其他开发/设备会话。同一原因失败两次后先诊断、缩小范围或换方案，继续可独立完成的工作。
- 完整输出写入本任务 `.local/<task>/`；对话只给关键结果、错误位置和必要片段。输出截断后改为筛选/分段读取，不重复整份输出；PowerShell 参数与引号按本机语法，不照搬历史 Bash 命令。
- 复用现有实现、依赖和验收脚本；修共享根因，不增加无需求抽象。先定义可观察结果，改动后审查 diff；已有有效证据可复用，代码变化或新失败才触发相应复验。
- 按变化选择检查：domain/db/obd 跑相关包测试；Python 诊断跑相关 `scripts.diagnostics.tests`；IPC 跑对应 selfcheck；界面跑对应 Electron 验收。已有脚本含构建时不额外重复构建；仅需要扩大覆盖时运行全量。
- 规则/MCP/文档变更检查语法、链接、适用范围、隐私与 `git diff --check`；核心 docs 另跑 `node scripts/docs-accept.mjs`。不为配置文字变更重复产品全套验收。
- `accept:all` 范围以脚本为准，不覆盖全部 OBD/Python/UI/实车。桌面 `LocatorGlbViewer.tsx` / three 的既有类型问题已于 2026-10-04 修复，独立检查用 `npm run check:types`；后续按实际报错和代码变化复验，构建成功不等于类型或实车验收通过。

## 工具与 MCP

- Codex 项目配置为 `.codex/config.toml`；`.cursor/mcp.json.example` 仅为旧客户端的空模板。以本会话实际提供的工具为准，不把模板或历史 MCP 名称当已连接服务。
- 仓库文件、Git、脚本和数据库检查优先内建文件工具/Shell/`packages/db`；不为查 SQLite 临时安装 MCP，不用 MCP 即席写业务库。
- 库 API 先核对已安装版本与源码，需要外部核实再查官方文档；已有合适文档 MCP 可用时直接调用，缺失则走 Web，不要求安装 context7/perplexity。
- Electron UI 优先已有项目验收脚本；交互浏览器使用本会话 Browser/CUA，或已有 `chrome-devtools`，单任务只选一套。缺 UI 工具时保留具体未验证项，不用页面打开成功替代验收。
- Issue/PR 用已有 GitHub connector 或已认证的 `gh`；本地 Git 不需要 GitHub MCP。提交、推送和第三方消息仍须明确授权。
- PETKA 远程操作只针对已核对的 `PORSCHE981_PETKA_SSH`/本机 SSH 配置；优先原生 `ssh`。具名 `ssh-mcp-petka` 确实可用时才调用，不改用全局调试机 `ssh-mcp`。未配置主机时只做本地工作，不猜连接参数。
- 按任务发现工具 schema，同一工具无需反复发现；不全量枚举资源/工具。连接失败先核对错误与配置；缺 server 直接用已有等价工具，不自动重挂、安装、扩大权限或延长超时。
- 本项目禁用全局 `mysql-mcp`、`ida-pro-mcp`、`ldj-debug`、`hcrdi-aichat` 与通用 `ssh-mcp`；需要具体能力时按任务另行评估。保留浏览器运行时及 UI 工具，不改全局配置、模型、速度或订阅。
