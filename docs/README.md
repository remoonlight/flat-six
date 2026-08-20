# docs/

| 文件 | 说明 |
|------|------|
| [requirements.md](./requirements.md) | 业务需求 v7（权威） |
| [progress.md](./progress.md) | 交付成果与收敛结论（teile / X-ray / flat-six） |
| [adr/001-no-ecu-write.md](./adr/001-no-ecu-write.md) | 不写 ECU |
| [adr/002-local-only-windows.md](./adr/002-local-only-windows.md) | 仅 Windows 本地 |
| [adr/003-petka-gui-explicit-only.md](./adr/003-petka-gui-explicit-only.md) | PETKA GUI 仅明示允许 |
| [adr/004-cms-rip-local-only.md](./adr/004-cms-rip-local-only.md) | CMS 抠模仅本机（App：引擎+底盘；不用 991 车身） |
| [cms-991-engine-rip.md](./cms-991-engine-rip.md) | CMS2021 Porsche DLC：991 引擎+底盘本机抠模 |
| [garage-3d-zone-pick.md](./garage-3d-zone-pick.md) | 车库 3D：分栏点穿点选（已实现，与代码同步） |

接棒先读 `requirements.md` + `../data/petka/README.md`，再跑 `npm run status:petka` / `status:cms-rip` / `status:mesh-map` / `accept:all`。  
3D 增强：`../data/seed/xray/README.md` · `../data/seed/flat-six/README.md`。
