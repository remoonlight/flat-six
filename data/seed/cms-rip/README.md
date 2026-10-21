# CMS rip 种子（无 mesh）

本目录只放**清单/约定**与可恢复的 mesh↔热点快照，不放游戏导出物（GLB）。

## 真源 vs 公开快照

| 角色 | 路径 | 说明 |
|------|------|------|
| **运行时真源** | `.local/cms-mesh-map.json` | gitignore；Locator「写入对应」写此处 |
| **可提交快照** | `mesh-map.template.json` | clone 后可恢复 mesh↔热点映射 |

- `manifest.json` — 目标车/引擎 ID、过滤关键字、本机路径约定（`bodyForApp: false`）
- `mesh-map.template.json` — mesh↔热点可恢复快照
- 导出物：`.local/cms-rip/`（见 [ADR 004](../../../docs/adr/004-cms-rip-local-only.md)）

CMS 既有参考入口只允许引擎与底盘，不使用 CMS 991 车身。当前车库透视机械层使用 [PETKA 图号分件](../petka-models/README.md)，车身／内饰见 [主模型说明](../flat-six/README.md)。Locator 保留既有单模型与 2D 对应能力；清单不代表缺失资产已补齐。

## 从 seed 恢复（PowerShell）

```powershell
New-Item -ItemType Directory -Force -Path .local | Out-Null
Copy-Item -Force data\seed\cms-rip\mesh-map.template.json .local\cms-mesh-map.json
```

```bash
npm run export:cms-rip
npm run status:cms-rip
```
