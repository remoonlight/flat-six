# 车库与定位模型成果

当前车库主模型、重复导出规则及来源边界集中在这里。具体对象键与全部修整操作以 [`refine-garage-primary.mjs`](../../../scripts/refine-garage-primary.mjs)、`garage-exterior-edits.json`及运行时 manifest 为准；不再沿用历次修改前的零件数量。

## 当前主模型

主模型为用户确认的 flat-six 981 车身、718 补件及 GT7／981 内饰 Blender 文件的保存可见组合，输出 `.local/garage-models/boxster-primary.glb`。按源文件当前场景、视图层和视口显隐导出，保留位置，不补入隐藏或其他模型。来源母版不覆盖；manifest 保存来源指纹、可见对象及拆分／合并／转归记录。

2026-10-11 明确要求携带当前模型提交，对上述 GLB 及去除本机绝对路径的 manifest 建立了精确白名单；已有 `.local/flat-six/` 和 `.local/petka-models/` 按现有白名单保存。该范围不扩展到 CMS、其他游戏导出物或 Blender 母版。见 [ADR 004](../../../docs/adr/004-cms-rip-local-only.md)。

## 已固化的模型整理

- 轮胎、扩散器、指定 Chassis、方向盘及指定仪表台块按确认组合合并；指定前灯、后保险杠及仪表台几何按规则拆分。共同父节点或几何整理保留源世界位置、材质与来源。
- 方向盘中心饰圈转归指定中心盖；指定车门件及仪表台分块保留原标识和分类规则。已恢复的两块遮阳板继续保留，其余确认删除的中控、缝线、除霜风口及内衬件保持删除。
- 右座椅由指定左座椅按车辆中线镜像，保留源几何和材质，不复制左侧 OEM 关联；既有右侧小件保留。
- 用户在 `车库外观.blend` 确认的外观删除与合并按编辑文件重放；跨分组移动时保留世界矩阵，来源分组随剩余子件更新。

以上是当前修整结果的归类，完整名称和操作顺序由整理脚本维护，不把每次增减的临时数量当作当前清单。

## 材质与车辆设置

外观车漆跟随车辆设置，软顶织物、内衬跟篷色、ROLLCAGE 跟车漆。内饰按明确关联及上部／下部配色、单色／双色／全真皮／GTS 包覆解释；固定塑料、地毯、泡沫、开关文字及具名电镀银覆盖分开处理。

- [内外饰材质复核](../vehicle/interior-material-review.json)：按明确 OEM 链接、模型分区和具名材质覆盖匹配，未关联表面保留来源材质；删除关联后恢复源材质，具名覆盖继续生效。
- [机械与备箱材质复核](../vehicle/xray-material-review.json)：按装配和模型匹配，恢复或更换关联时重新解释。

表面参数和程序纹理是屏幕近似。982 手动换挡头等跨车型资料只作为相应表面参考，不作为 981 PDK 或 OEM 适用性证明。材质调整不补几何或改写用户对应关系。

## 导出与复核

用 Blender 后台运行 [`export-garage-primary.py`](../../../scripts/export-garage-primary.py)，显式传入 `--source`。导出后执行同一整理规则；独立运行 `scripts/refine-garage-primary.mjs` 默认预览，显式 `--apply` 才改本机 GLB／manifest。重复导出必须保留本次确认的对象规则和来源记录。

## Locator 回退与对照模型

[`cabins.json`](cabins.json)保存四套座舱与提示；原 flat-six 资源继续用于缺资源回退和 Locator，不能用旧座舱说明覆盖当前车库主模型。

- `cabin-981-boxster`：Locator 内饰手拼主用，材质子块级；不冒充座椅／方向盘级 OEM 拆件。
- `cabin-982-boxster`：内饰对照；`cabin-982-cayman`：方向盘对照。
- `cabin-981-cayman`：外板对照，不参与内饰手拼。

[`interior-stitch.plan.json`](interior-stitch.plan.json)仍为草稿；`npm run apply:interior-stitch` 把 proposedLinks 写入本机映射，不增加资产完成度。检查入口为 `node data/seed/flat-six/interior-stitch.selfcheck.mjs` 与 `node apps/desktop/electron/cms-assets.selfcheck.mjs`。

`npm run fetch:flat-six-cabins` 按座舱清单获取资源；`npm run fetch:flat-six-garage` 按 [`garage-models.json`](garage-models.json)获取对照外观。`fs-*` 浏览模型保持 `browseOnly`，不进入分区默认模型。

## 来源许可

产品幽灵壳和原 Locator 内饰手拼主用 CC BY 4.0 的 `boxster-real.glb`。部分 Spyder、GT、991 对照资源为 CC BY-NC-SA 4.0，仅按其本机非商用范围使用。当前混合主模型按用户具名精确例外保存；不据此扩大其他资源的分发范围。幽灵车壳仍按 [X-ray 说明](../xray/README.md)使用 981 车身。
