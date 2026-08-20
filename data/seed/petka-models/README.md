# 981 PETKA 图号 GLB（本机）

分件重建。**不提交** GLB；清单见 [`chassis-exhaust.json`](./chassis-exhaust.json)。

| 前缀 | system | X-ray 组 |
|------|--------|----------|
| `1xx-*` | `engine` | **引擎+燃油系统** (`power`) |
| `2xx-*` | `exhaust` | **引擎+燃油系统** (`power`) |
| `3xx-*` | `driveline` | **传动系统+前后轮** (`drive`) |
| `4xx-*` / `5xx-*` | `chassis` | **传动系统+前后轮** (`drive`) |

## 本机路径

- 权威缓存：`.local/petka-models/*.glb`
- 投喂：`data/model/` → `npm run archive:petka-models`
- 装配：`data/seed/xray/assemblies.json`（X-ray 微调 / Locator）与 `garage-assemblies.json`（车库透视）
- **现阶段**：机械层 = 水冷 + 引擎（`010-000`+`107-010`）+ 悬架 + 传动 + `402-000`；气路 = 进气（`105-020`）+ 排气（`202-*`）；**车库透视不含 CMS MA1.03**
- 传动 → `.local/petka-models/merged/driveline.glb`（`pm-302-000`）
- 悬架 → `.local/petka-models/merged/suspension.glb`（含 401… + 501-000/001/003 + 502）
- 引擎 → `.local/petka-models/merged/engine.glb`（010 + 107 + 105，含 part2_R / part0_R）
- bake 层/子 mesh TRS；冲突名加 `_405` / `_4205` / `_403` / `_5010` / `_5011` / `_5013` / `_5015` / `_502` / `_107` / `_105` / `_205`

## 口径

- 非 OEM；车壳仍 flat-six；机械层无 CMS
- X-ray 微调窗口右侧按组列出 + 幽灵车壳
