# X-ray 多源拼装（flat-six 车坐标）

坐标系与 [flat-six `xray-assemblies.ts`](https://github.com/dmitry-grechko/flat-six/blob/main/components/garage/xray-assemblies.ts) 一致：原点≈车质心，**+Z 车头**，**+Y 上**。`axle` 见 [`assemblies.json`](./assemblies.json)。

**产品口径**：Locator / **X-ray 微调** / 车库透视机械层均为 `.local/petka-models/` 图号分件（两组：**引擎+燃油** · **传动+前后轮**）；幽灵车壳仍 flat-six。**不用 CMS 991 车身/机械**。

## 真源 vs 公开快照

| 角色 | 路径 | 说明 |
|------|------|------|
| **运行时真源** | `.local/xray-transforms.json` 等 | gitignore；App 读写此处 |
| **可提交快照** | 本目录 `*.template.json` / `*.seed.json` | clone 后可恢复姿态/链接，避免公开发布丢调参 |

手调完成后，把 `.local` 内容回写进对应 template/seed，再提交（勿提交 `.local` 本身）。

## 文件

| 路径 | 用途 |
|------|------|
| `assemblies.json` | Locator + X-ray 微调（petka + CMS MA1.03 + 车壳） |
| `garage-assemblies.json` | 车库透视（同批；透视时藏车身） |
| `garage-flows.json` | 车库流程/装配流 |
| `transforms.template.json` | 层 TRS 可恢复快照 → `.local/xray-transforms.json` |
| `mesh-state.template.json` | 子 mesh TRS/可见快照 → `.local/xray-mesh-state.json` |
| `model-oem-links.seed.json` | 模型块↔OEM 链接快照 → `.local/model-oem-links.json` |
| `.local/xray-transforms.json` | 本机层 TRS（gitignore，真源） |
| `.local/xray-mesh-state.json` | 本机子 mesh 状态（gitignore，真源） |
| `.local/model-oem-links.json` | 本机 OEM 链接（gitignore，真源） |
| `.local/petka-models/*.glb` | 981 分件源 |
| `.local/petka-models/merged/*.glb` | 已 bake：cooling / fuel / engine / suspension |
| `.local/cms-rip/engine_b61_porsche/engine.glb` | CMS 991.2 MA1.03 引擎（可调） |
| `.local/flat-six/boxster-real.glb` | 车身 / 幽灵壳 |

## 从 seed 恢复姿态（PowerShell）

在仓库根目录执行：

```powershell
New-Item -ItemType Directory -Force -Path .local | Out-Null
Copy-Item -Force data\seed\xray\transforms.template.json .local\xray-transforms.json
Copy-Item -Force data\seed\xray\mesh-state.template.json .local\xray-mesh-state.json
Copy-Item -Force data\seed\xray\model-oem-links.seed.json .local\model-oem-links.json
# CMS mesh map 见 ../cms-rip/README.md
```

编辑后仍写回 `.local/`；需要入库时再覆盖上述 seed 文件。

## 手调位置（运行时）

```powershell
# 若尚无 .local 覆盖，先按上一节从 seed 恢复
# 编辑 layers.<assemblyId>.position|rotationEuler|scale
# 车库：零件浏览器 → 透视 → 选层拖滑条（整体缩放）或「透视微调」窗口
```

车库缩放滑条为**整体等比**（写入 `[s,s,s]`）。层 TRS → `.local/xray-transforms.json`；子 mesh → `.local/xray-mesh-state.json`。
