# porsche981 — Agent map

Windows 本地 Electron 车库：**2014 Boxster S（981）PDK**。无云、无账号。权威需求：`docs/requirements.md`；进度：`docs/progress.md`。

## Packages

| Path | Role |
|------|------|
| `apps/desktop` | Electron UI + `electron/` main/preload/IPC |
| `packages/domain` | 纯业务逻辑（CSV、间隔、货币等） |
| `packages/db` | SQLite（`node:sqlite`）schema / 查询 |
| `data/seed/` | 可提交种子；含 `x431/plaintext-archive/`（设码明文源）；`data/petka/*/parts.csv` 本机权威（gitignore） |
| `docs/research/can-code/` | CAN/X431 研究笔记 |
| `.local/` | 运行时库、inbox、bulk、CMS/flat-six 缓存 — **勿提交** |

## 常用命令

- `npm run dev` · `npm test` · `npm run accept:all`
- PETKA：`status:petka` · `ingest:petka` · `parse:petka-bulk` · `sync:petka-bulk-remote`
- 3D：`status:mesh-map` · `status:cms-rip` · `accept:ploc2` · `archive:petka-models`

## 硬边界（详见 ADR）

1. **不写 ECU**（设码/OBD 只读）— `docs/adr/001-no-ecu-write.md`
2. **仅本机 Windows + SQLite** — `docs/adr/002-local-only-windows.md`
3. **PETKA GUI 默认禁止**；仅当用户本会话明示授权 — `docs/adr/003-petka-gui-explicit-only.md`
4. **CMS 抠模仅本机**；产品不用 991 车身 — `docs/adr/004-cms-rip-local-only.md`
5. **禁止**解析/入库 PETKA `DATA\PO` / `.zgd`；只收明文导出/剪贴板/inbox
6. UI 价 **一律折算 CNY**（`data/seed/fx.json`）；teile/Design911 ≠ PETKA 已核

## 数据事实（易错）

- 保养子集（~20 sku）与 **981/982 全量目录**是两套数据；全量走 `.local/petka-bulk/` + `generation` 筛选
- PETKA 主机用本机 SSH / `PORSCHE981_PETKA_SSH`（MCP `ssh-mcp-petka`）；本机未必装 EtStart
- DB 开发路径：`.local/garage.db`；改 schema 走 `packages/db`，勿用 MCP 乱写生产语义数据
