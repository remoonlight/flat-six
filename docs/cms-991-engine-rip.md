# CMS2021 Porsche DLC → 991 引擎 / 底盘（本机资产说明）

> ADR：`docs/adr/004-cms-rip-local-only.md`  
> 清单：`data/seed/cms-rip/manifest.json`  
> 状态：`npm run status:cms-rip`

## 用了什么、为何不进产品

| 项 | 说明 |
|----|------|
| 来源 | Car Mechanic Simulator 2021 + Porsche Remastered DLC |
| 车型参照 | 2016 911 Carrera S（**991.2**），引擎内部 ID `engine_b61_porsche`（B6 Twin-Turbo **MA1.03**） |
| App 挂载 | **仅** 本机导出的 `engine.glb` + `chassis.glb`（目录 `.local/cms-rip/`，gitignore） |
| **不用** 991 车身 | 产品幽灵壳 / 内饰走 flat-six（见 `data/seed/flat-six/`）；991≠981 |

**不是** 981 Boxster 的 MA1.23 NA；仅作 flat-6 / 底盘示意。964 / Carrera GT 等不在范围。

导出物与第三方游戏资产版权归属原权利方；**禁止**把抠模结果提交进仓、上传网盘或当公开资源再打包。本仓库只记录「本机可选用哪些文件、产品边界」，不提供可复现提取教程。

## 本机就绪检查（无教程）

已自行取得合法游戏拷贝并在本机完成导出后：

```bash
npm run status:cms-rip   # engineGlb / chassisGlb；bodyForApp 应为 false
```

缺 GLB 时 Locator 3D 区走 placeholder；2D / 其它页面仍可用。模板：`data/seed/cms-rip/mesh-map.template.json`。

## 与 App

Locator：3D（three.js）+ 2D 爆炸图。默认 zone→模型：`engine-bay`→引擎，`brakes`/`chassis`→底盘。mesh 对应写入 `.local/cms-mesh-map.json`（gitignore）。

X-ray 拼装坐标系见 [`data/seed/xray/README.md`](../data/seed/xray/README.md)；姿态模板 `transforms.template.json`，本机覆盖 `.local/xray-transforms.json`。

自检：`node apps/desktop/electron/xray-assets.selfcheck.mjs`。

## 禁止

- `git add` 任何导出 mesh/贴图  
- 公开再分发本抠模结果  
- 用本模替换 PETKA OEM 号  
- 把 991 车身当产品幽灵壳  
