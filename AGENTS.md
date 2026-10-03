# porsche981 — Agent map

Windows 本地 Electron 车库：**2014 Boxster S（981）PDK**。无云、无账号。文档入口：`docs/README.md`；权威需求：`docs/requirements.md`；进度：`docs/progress.md`；OBD 当前结果：`OBD_STATUS.md`。

Codex 直接执行；禁止 Cursor CLI / Grok；仅用户为当前任务明确要求委派时使用允许的 Codex worker。历史工作单与迁入记录不构成授权。`docs/` 核心文档以 `.gitignore` 白名单纳入版本管理，设备记录与私人历史继续本地保存；文件归属与未闭合问题见 `docs/convergence-review.md`。

## Packages

| Path | Role |
|------|------|
| `apps/desktop` | Electron UI + `electron/` main/preload/IPC |
| `packages/domain` | 纯业务逻辑（CSV、间隔、货币等） |
| `packages/db` | SQLite（`node:sqlite`）schema / 查询 |
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
