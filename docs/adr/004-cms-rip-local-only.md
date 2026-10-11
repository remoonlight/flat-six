# ADR 004: CMS2021 抠模仅本机私用

## Context

定位增强需要可旋转的引擎/底盘示意 mesh。公开网几乎无 9A1/MA1 工程级模型。用户允许从正版 **Car Mechanic Simulator 2021 + Porsche Remastered DLC** 抠 **991.2** 的引擎（`engine_b61_porsche` / MA1.03）与悬架·制动等底盘装配，作本机示意。

**车身**不采用 CMS 991 板件：产品幽灵壳 / 外板用 **flat-six 981**（CC BY）；内饰用 flat-six 座舱（含 982 本机保留筛选用）。

2026-10-11 用户更新车库外观／内饰选择：以已确认的 flat-six 981 车身＋718 补件＋GT7 内饰 Blender 文件当前保存的可见组合为主，按其场景、视图层和视口显隐保留零件位置；外观车漆按项目车辆设置显示，其余材质保留，后续明确指定的软顶织物、内衬跟篷色及 ROLLCAGE 跟车漆调整见 [业务需求](../requirements.md)。不自动补入其他模型或开启隐藏件。此新主用组合保存在本机 `.local/garage-models/`，不提交或传播游戏派生资源；原 flat-six 模型保留作回退及 Locator 原有用途。CMS 991 车身禁用与本 ADR 的 CMS 导出范围保持，混合车型视觉模型不证明本车 OEM 适用性。

## Decision

2026-10-11 用户随后明确要求 commit＋push 项目并携带模型、零件号对应及价格。本次对当前 `garage-models/boxster-primary.glb` 和去除本机绝对路径的 manifest 建立精确提交例外；可提交种子保存对应及微调，价格沿用已有明文目录和源币种。该请求更新了上文当前车库主模型“不提交”的范围，不扩大 CMS 抠模资源范围，也不构成混合模型 OEM 适用性验证。

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
