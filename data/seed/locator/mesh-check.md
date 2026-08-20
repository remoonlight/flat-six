# 3D 核对 SOP（Phase 1 · 口径 A）

> 姿态/示意闸门，**不做 OEM 下单抽检**。报告：`npm run status:mesh-map` → `.local/mesh-map-status.json`。

## 1. 跑状态

```bash
npm run status:mesh-map
```

看摘要：`glb` / `transforms` / `offMap` / `eng-*=N` / `next=`。缺口在 `nextActions`。

## 2. 姿态手调（侧 / 俯 / 穿模）

1. Locator 开 **X-ray**，单击引擎/底盘/车壳选中总成（或用「微调层」下拉）
2. 下方滑条分别拖 **X/Y/Z** 与 **RX/RY/RZ**（自动写入 `.local/xray-transforms.json`，勿提交）
3. 侧视 / 俯视看：是否穿模、悬架是否错位；「归零」可撤回该层手动偏移
4. 再跑 `npm run status:mesh-map`

## 3. eng-* mesh 手对

1. Locator 切**单模型 engine**
2. 点选进气 / 本体 / 排气相关 mesh →「写入对应」到 `eng-intake` / `eng-block` / `eng-exhaust`
3. 零件仍挂 `engine-bay` 热点；分块仅导航
4. 再跑 `status:mesh-map`，确认 `engHotspotMeshLinks` 非全 0

## 4. 热点够用

保养件有 `locator_hotspot` 且落在 `zones.json` 即可；不要求每个 mesh 绑 sku。`offMap` 应尽量空。
