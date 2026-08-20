# 社区间隔种子（审计台账）

## 文件

- `community-draft.json`：带 `interval_km` / `interval_months` 的零件间隔台账，`audit_status` 为 `pending` 或 `audited`。可选 `interval_kind`：`soft`（社区软提醒）或省略/`hard`（硬周期）。

## 与 parts 的关系

- 运行时到期计算读的是 SQLite `parts.interval_*`（bootstrap / PETKA CSV 入库）。
- 本目录是**审计台账**：标明哪些间隔仍是草稿、哪些已人工确认，并写清来源/原因。
- 改间隔时：先改 `community-draft.json`，再运行 `npm run sync:intervals`（把 km/月同步到 `data/seed/parts/bootstrap.json`），或对应 PETKA CSV 后重新 ingest/seed。

## 如何审计

1. 对照手册 / PCNA 保养清单 / PETKA / 社区多源交叉核验某 SKU 的公里与月限。
2. 在 `community-draft.json` 中更新数值，将 `audit_status` 改为 `audited`，在 `notes` 写清来源。
3. 证据不足保持 `pending`，`notes` **必须**写明原因（验收会检查）。
4. 厂方仅为「检查/按磨损/无固定更换」的项：可标 `audited`，设 `"interval_kind": "soft"`，`notes` 必须写明厂方口径 + 本 App 数值是社区软提醒（非厂方周期）；**不要**编造 OEM 价。`sync:intervals` 会把 `interval_kind=soft` 写入 `parts.notes`；Garage 对 soft 项显示「软提醒/按磨损」而非 overdue 红 pill。
5. 运行 `npm run sync:intervals`，再 `npm run accept:interval`。
6. **不要**用假交车日推算无更换史零件的到期。

## 审计台账（2026-07-31）

16 条全部 `audited`（此前 6 条 pending 已收敛）：

| SKU | 厂方口径 | App 数值含义 |
|-----|----------|--------------|
| front/rear-brake-pads | 目视磨损检查，无固定里程 | 40k / 50k km 社区软提醒 |
| coolant | Check level/antifreeze | 48 月社区软提醒（寿命液说法） |
| battery | Check condition | 48 月社区寿命软提醒；换后须登记 |
| fuel-filter | 清单无更换；箱内集成 | **非定期/须 VIN**；60k/48 仅软提醒占位；价空 |
| tire-fl | Check condition/pressure | 40k km / 6 年软提醒；无单一 OEM，价空 |

其余 10 条（机油/滤、空滤、空调滤、火花塞、制动液、多楔带、雨刮、PDK 两油）见各条 `notes`（厂方周期或已标清的社区保守值）。

## 无更换史规则

无 `service_records` 时：`status=no_baseline`，剩余公里/天为 `null`，UI 提示「需登记首次更换」。
