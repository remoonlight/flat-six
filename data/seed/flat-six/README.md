# flat-six 3D / 座舱（本机缓存）

模型按 `.gitignore` 精确白名单提交，清单见 [`cabins.json`](./cabins.json)。2026-10-11 用户明确要求携带当前模型 commit＋push，增加 `.local/garage-models/boxster-primary.glb` 和可移植 manifest；其余运行时资源与 CMS 抠模仍保留本机。

桌面外观编辑回带规则：`garage-exterior-edits.json` 保存用户在 `车库外观.blend` 中确认的删除及合并，主模型整理脚本在既有拆分／合并后重放。仅调整外观节点组织，跨原分组移动子件时保持世界矩阵、原网格和材质；既有分组来源随剩余子件更新，内饰不变。当前外观 124、内饰 859 个可选零件。

门板追加：左右 `054` 的原独立拉手／扶手区域采用车辆设置第二色，上部保持主色；左右 `092` 固定拉丝银。`Source__hq_body_116_T__part_025` 功能分栏修正为车门饰板，标识不变。新增关联、内衬 `Part_005`、安全带／锁扣及车内后视镜等材质见 `data/seed/vehicle/interior-material-review.json`。前后备箱及透视显式关联材质见 `data/seed/vehicle/xray-material-review.json`，按装配＋模型匹配，恢复／换关联时重新解析；不改源几何或补入缺失模型，表面参数为视觉近似。

2026-10-11：车库外观／内饰按用户指定的 flat-six 981 车身＋718 补件＋GT7 内饰 Blender 文件（当前 `981_Boxster_GT7_merged_R_split.blend`）保存的可见组合导出，资源位于 `.local/garage-models/boxster-primary.glb`，按本日后续明确 commit＋push 要求提交。以文件当前场景、视图层及视口显隐为准，保留位置，不自动补入其他模型；外观车漆跟随项目车辆设置的颜色和漆面参数，其余材质保留源模型外观。车身和轮组归外观，内饰独立分类。此目录的原模型继续保留，供缺资源回退及 Locator 使用；以下早期主座舱结论按该范围理解。导出入口为 `scripts/export-garage-primary.py`（Blender 后台执行，显式传入 `--source`）；母版不覆盖，当前场景、视图层、可见对象及来源哈希写入本机 manifest。现有 `.local/flat-six/` 白名单不用于存放游戏来源派生模型。

后续明确调整见 [`requirements.md`](../../../docs/requirements.md)：软顶织物、内衬跟篷色、ROLLCAGE 跟车漆；四对轮胎各合为一个可选零件，指定前保险杠灯具拆为左右两件，并移除两块指定中控缝线。`scripts/refine-garage-primary.mjs` 默认仅预览，加 `--apply` 才更新本机 GLB／manifest；Blender 导出结束后也会执行同一整理规则。保留原位置、材质及来源记录，不覆盖 Blender 母版。

追加规则：指定后保险杠左右 `side_details` 各拆成三个独立部件；`718__GEO_Chassis_SUB1_Part_002`、`Part_010`、`Part_012` 合为 `718__GEO_Chassis_SUB1_Part_002_010_012`，作为一个列表、点选和微调对象。

仪表台 `Cabin__dashboard__dashboard__interior_041__01` 按不相连的几何拆成六件，保留内饰／仪表台分类，名称追加 `分块01` 至 `分块06`。

方向盘 `steeringWheel__005_070__assembly` 与 `steeringWheel__interior_071` 合为一个整体，保留前者名称、内饰／仪表台分类及已有 OEM 关联。

座椅分栏追加移除：`718__GEO_INT_LEATHER_SUB1_Part_007`、`Part_006`，以及 `718__GEO_INT_PLASTIC_BLACK_SUB0_Part_029`、`Part_114`、`Part_028`、`Part_085`；仪表台 `dashboard__interior_041__01` 的分块 03／04／05 追加合为 `分块03_04_05`，01／02／06 保持独立。合并在拆分后执行，保留三块局部位置、几何和材质；同一整理脚本保证重新导出后继续生效。

方向盘追加修正：`steeringWheel__005_070__assembly` 中的中心气囊盖细饰圈转归 `steeringWheel__interior_004__01`，通过共同父节点统一点选、微调和隐藏；轮圈／辐条保留原总成。`steeringWheel__interior_072` 按不相连几何拆成六块。整理脚本保留三角形属性、贴图、材质和世界位置，并记录转归来源；保留 004 名称，转归的细饰圈不再继承 005 的 OEM／材质归属。

左门饰板追加移除：`leftDoorTrim__interior_064__corrected__part_011` 与 `part_003`，其他零件保持；删除规则也在同一整理脚本中。

后续恢复：用户要求恢复之前删除的两块大面积遮阳板，已恢复 `718__GEO_INT_LEATHER_SUB1_Part_006` 和 `Part_007` 的原节点／几何／位置，整理脚本撤销这两件的删除规则；其余移除继续生效。当前组合为外观 201、内饰 868，共 1,069 个可选零件。

本次新增关联补充 18 个 OEM 材质规则，累计 55 个。浏览器分栏按复核规则显示遮阳板和 B 柱件为内衬、通道行李网为中控台、下仪表板为仪表板；保持原模型名称和微调／关联键。电镀银饰条、包覆、塑料、开关文字和网布分别处理。换挡参考号 `982711141AJ8YR` 的资料为 982 手动黑皮换挡头，仅支持表面参考，不能作为 981 PDK 适配证据。

右门原有 `Cabin__dashboard__Source__hq_body_116_T__part_004` 通过已有右门 OEM 关联归到车门分栏，修正车门视图变淡／不能点选的问题；保留原标识及左右对称位置。用户指定左右门 `interior_041__01` 与仪表台分块 03／04／05 同为电镀银，使用两个具名材质覆盖，避免按整块皮革门板 OEM 扩大着色范围。

后扩散器追加合并：`rear_diffuser_L_shell`、`exhaust_surround`、`rear_diffuser_R_shell` 合为 `Exterior__rear-bumper__SM_RearKit_0000003_MAT_Details_MAT130_0__rear_diffuser_assembly`，通过无变换的共同父节点统一选择／移动／显隐，保留三块原几何、材质和位置。规则在重新导出后继续生效；当前组合为外观 199、内饰 868，共 1,067 个可选零件。

Chassis 追加合并：`718__GEO_Chassis_SUB1_Part_003`、`Part_011`、`Part_013` 合为 `Exterior__other__718__GEO_Chassis_SUB1_Part_003_011_013`，通过无变换共同父节点统一选择／移动／显隐，保留三块原几何、材质和位置。规则在重新导出后继续生效；当前组合为外观 197、内饰 868，共 1,065 个可选零件。

后续追加删除中控台 `centerConsole__interior_009__01__part_001`／`002`／`437`／`438` 及左右 `defrostVent__assembly`，并将 `leftSeat__interior_009__01` 按车辆中线 X=0 镜像为 `Cabin__seats__rightSeat__interior_009__01__mirrored_from_left`。镜像沿用源网格／材质，以反射变换放置，保留原有右侧小件；新件不复制左侧 OEM 关联。删除与镜像在同一整理脚本中执行，重新导出后继续生效。当前组合为外观 197、内饰 863，共 1,060 个可选零件。

仪表台同色修正：`dashboard__interior_016__01` 和 `dashboard__interior__assembly` 的上部表面（含分离的副驾驶气囊盖）统一跟随车辆设置上部颜色／包覆材质，明确关联的独立仪表罩仍使用其对比区域规则。追加删除内衬 `718__GEO_INT_PLASTIC_BLACK_SUB0_Part_272`，整理脚本保留这一删除。当前组合为外观 197、内饰 862，共 1,059 个可选零件。

内饰已关联 OEM 号的表面复核记录在 [`interior-material-review.json`](../vehicle/interior-material-review.json)。运行时按明确关联匹配主模型，包覆区域跟随车辆设置的单色、双色套装、全真皮双色或 GTS 材质，保留塑料、地毯、泡沫、碳纤维及开关文字的区别；除用户具名材质覆盖外，未关联表面保留来源材质。分区只调整原几何的材质组，零件清单、点选及微调对象保持。关联删除后恢复源材质，用户具名覆盖继续生效；几何与用户关联文件不随此次复核改写。网上资料支持材料和配色布局，纹理及反射为程序生成的屏幕近似，不能作为原厂实测或跨车型适配证据。

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
