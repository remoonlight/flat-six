# flat-six 3D / 座舱（本机缓存）

本仓库**不提交** GLB（`.local/` gitignore）。清单见 [`cabins.json`](./cabins.json)。

**产品口径（2026-08-01）**：车身幽灵壳用 981 Boxster；内饰**已筛**（主用 `cabin-981-boxster`）；982 仅对照。手拼种子见 [`interior-stitch.plan.json`](./interior-stitch.plan.json)。不做 App 内 981/982 世代切换。CMS 991 车身**不用**。

## 座舱两套（981 主用 · 982 筛选保留）

| id | 世代 | 本机文件 | 许可 | 用途 |
|----|------|----------|------|------|
| `cabin-981-boxster` | **981** | `boxster-real.glb` | CC BY 4.0 | **当前主座舱/幽灵壳**（`SM_Interior_*`） |
| `cabin-981-cayman` | 981 | `cayman-981.glb` | CC BY 4.0 | 备选（舱弱；外板对照） |
| `cabin-982-boxster` | **982/718** | `boxster-s-982.glb` | CC BY 4.0 | **保留筛选用** |
| `cabin-982-cayman` | 982/718 | `cayman-982.glb` | CC BY 4.0 | 保留筛选（含方向盘细节节点） |

手拼：Locator 单模型点选 mesh → 写入 `.local/cms-mesh-map.json`。免费模**不是** OEM 零件级。

## 内饰筛选结论（2026-08-01）

对 `.local/flat-six/*.glb` 解析 glTF JSON 的 node/mesh 名，对照 [`cabins.json`](./cabins.json) `cabinMeshHints`。

| 模型 | 内饰可分点程度 | 推荐用途 | 下一步 |
|------|----------------|----------|--------|
| `cabin-981-boxster` | **中**（`SM_Interior_*` 材质子块 + SoftTop） | **981 主用手拼** | 按 [`interior-stitch.plan.json`](./interior-stitch.plan.json) 在 Locator 手对 |
| `cabin-982-boxster` | 中低（Interior / SeatBelt / 色区，块少） | **982 对照** | `controlHints` 仅对照；不进默认映射 |
| `cabin-982-cayman` | 低（仅 `steering_wheel_details`） | 982 方向盘对照 | 同上 |
| `cabin-981-cayman` | **无**（无 Interior 命名） | 外板对照 | 不参与内饰手拼 |

**排序**：手拼内饰热点 → ① `cabin-981-boxster`（主）→ ② `cabin-982-boxster`（对照）→ ③ `cabin-982-cayman`（方向盘）→ ④ `cabin-981-cayman`（外板）。

### 手拼种子（已接 Locator interior 草稿）

- 计划：[`interior-stitch.plan.json`](./interior-stitch.plan.json)（`status: draft`，`assetId: cabin981`）
- Locator：`zones.json` 已含 `interior`（P-Loc-2 草稿，不挡 P-Loc-1）；默认模型 `cabin981`；2D 热点对齐 plan
- 灌映射：`npm run apply:interior-stitch`（proposedLinks → `.local/cms-mesh-map.json`，幂等）
- 粒度诚实：仅材质子块级（Details_INT / Light / SoftTop 等），非座椅/方向盘级 OEM
- 自检：`node data/seed/flat-six/interior-stitch.selfcheck.mjs`；catalog：`node apps/desktop/electron/cms-assets.selfcheck.mjs`

## 下载

```bash
# 产品座舱四套（cabins.json）
npm run fetch:flat-six-cabins

# flat-six.org/garage 全部独立外观 GLB（garage-models.json，含 987/982/991 与 NC-SA）
npm run fetch:flat-six-garage
```

清单：[`garage-models.json`](./garage-models.json)（对标站点 `lib/models.ts` + `NOTICE.md`；不含 Audi admin-only）。Locator → 单模型可选 `fs-*`（一律 `browseOnly`，不进 zone 默认）。

## 许可

- 产品幽灵壳/内饰手拼主用 **CC BY 4.0** 的 `boxster-real.glb`。
- 车库对照浏览可含 **CC BY-NC-SA 4.0**（Spyder / 部分 GT / 991 等）：仅本机非商用；勿再分发衍生。
- X-ray 幽灵车壳仍用 `boxster-real.glb`（见 [`../xray/`](../xray/README.md)）。
