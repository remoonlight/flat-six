# ADR 004: CMS2021 抠模仅本机私用

## Context

定位增强需要可旋转的引擎/底盘示意 mesh。公开网几乎无 9A1/MA1 工程级模型。用户允许从正版 **Car Mechanic Simulator 2021 + Porsche Remastered DLC** 抠 **991.2** 的引擎（`engine_b61_porsche` / MA1.03）与悬架·制动等底盘装配，作本机示意。

**车身**不采用 CMS 991 板件：产品幽灵壳 / 外板用 **flat-six 981**（CC BY）；内饰用 flat-six 座舱（含 982 本机保留筛选用）。

## Decision

1. **仅正版已安装游戏**；不破解、不绕 DRM、不传播资产。
2. 导出物只落 **`.local/cms-rip/`**（已在仓库 `.gitignore` 的 `.local/` 下）；**禁止**提交 mesh/贴图/AssetBundle。
3. 业务 OEM / 热点仍以零件主数据 + 本车 VIN 为准；游戏 mesh **不得**冒充 981/MA1.23 零件真相（991.2 为双涡轮 MA1.03，后置包装；悬架为游戏通用装配）。
4. **App 挂载范围**：仅 **engine.glb + chassis.glb**。**不用** `porsche991_body/body.glb`（即便本机曾导出也不进产品目录）。
5. App 若加载 GLB：路径指向 `.local/…`，缺文件则降级占位 / PETKA overview。
6. 自动化入口：`npm run export:cms-rip`（UnityPy）；状态：`npm run status:cms-rip`。

## Consequences

- 管线文档：`docs/cms-991-engine-rip.md`
- 目标清单：`data/seed/cms-rip/manifest.json`
- 脚本：`scripts/cms_rip_export.py`、`scripts/cms-rip-status.mjs`
- 车身/内饰：`data/seed/flat-six/` · X-ray：`data/seed/xray/`
