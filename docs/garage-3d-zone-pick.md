# 车库 3D：分栏焦点下的 mesh 点选

> **已实现 · 与代码同步。**  
> 实现：`apps/desktop/src/components/LocatorGlbViewer.tsx`  
> 入口：零件浏览器车库 3D（同 viewer 的 OEM 关联窗共用逻辑）。

## 人话

选了分栏后，非焦点会半透显示。点选时这些半透 mesh **当空气**（射线穿过、不挡点），只有当前焦点 mesh 能被点中。

内饰再加一条：**顶篷永远点不着**（含「全部」）；只能点舱内 `SM_Interior`。

## 规则（与代码一致）

| 模式 | 何时打点穿标 | 可点 | 点穿（忽略几何） |
|------|----------------|------|------------------|
| 外观 · 非「全部」 | `applyExteriorZoneFocus` | 分栏 `match` 命中 | 其余 mesh |
| 外观 · 「全部」 | 不打标（`clearZonePickMarks`） | 平常点选 | — |
| 内饰 · 任意分栏（含「全部」） | `applyInteriorZoneFocus` | `SM_Interior` ∩ 分栏 `match`，且**非**顶篷 | 顶篷 + 外板等非舱内 + 非本分栏舱内 |
| 透视 xray | `clearZonePickMarks` | 平常点选（再 solid 优先 ghost） | — |

顶篷判定：`isSoftTopMeshName`（`softtop` / `convertible` / `dach` / `hood fabric` 等）。

## 实现要点

- `markZonePick(focus)`：焦点 → `zoneFocus` + 默认 `raycast`；非焦点 → `pickIgnore` + `raycast = () => {}`（不进 `intersects`）。
- `clearZonePickMarks`：切模式/分栏前清标、恢复 `Mesh.prototype.raycast`。
- `onClick`：`intersectObject` 后仍滤 `pickIgnore`，再 **非 ghost 优先于 ghost**。
- **不靠 opacity 阈值**判可点性（避免误伤玻璃等）。
- 非焦点仍半透**可见**，不隐藏。

相关函数：`markZonePick` / `clearZonePickMarks` / `applyExteriorZoneFocus` / `applyInteriorZoneFocus` / `onClick`。

视觉透明度（仅对照，非点选依据）：

- 外观非焦点 `opacity ≈ 0.12`
- 内饰非焦点 `≈ 0.14` / 焦点 `≈ 0.92`

## 验收清单

| # | 标准 | 状态 |
|---|------|:----:|
| A1 | 外观非「全部」：点纯半透非焦点 → 不选中 | ✅ |
| A2 | 半透挡在焦点前 → 点穿选中后面焦点 | ✅ |
| A3 | 直接点焦点 → 正常选中 | ✅ |
| A4 | 内饰非「全部」同 A1–A3（可点范围为舱内分栏） | ✅ |
| A5 | 非焦点仍半透可见 | ✅ |
| A6 | 有分栏点穿时：先忽略非焦点，再 solid 优先 ghost | ✅ |
| A7 | **内饰「全部」：点顶篷不选中**（点穿） | ✅ |
| A8 | 外观「全部」 / 透视：恢复平常点选 | ✅ |

手测路径：车库 · 零件浏览器 → 外观/内饰分栏 → 点 3D。

## 明确不做

- 不改车辆设置色板。
- 不引入新依赖。
- 内饰不把顶篷当可选 OEM 热点（点选忽略）；零件浏览器内饰由 `hideSoftTop` + `applyInteriorZoneFocus` 隐藏顶篷。

## 已收敛结论（原质疑归档）

1. **极性**：半透/非焦点 = 点击忽略（点穿）；不是「实色跳过」。
2. **判定**：`zoneFocus` / `pickIgnore` + 空 `raycast`，不用 opacity。
3. **全部**：外观关点穿；内饰仍点穿非舱内与顶篷。
4. **语义**：忽略几何做点击，不是命中后再丢弃选中。
5. **OEM 窗**：共用 `LocatorGlbViewer` 则同一套规则。
