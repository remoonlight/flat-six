# docs/

| 文件 | 说明 |
|------|------|
| [requirements.md](./requirements.md) | 业务需求 v7（权威） |
| [progress.md](./progress.md) | 交付成果与收敛结论（teile / X-ray / flat-six） |
| [adr/001-no-ecu-write.md](./adr/001-no-ecu-write.md) | 禁止编码/刷写；明确范围的故障码清除为唯一例外 |
| [adr/002-local-only-windows.md](./adr/002-local-only-windows.md) | 仅 Windows 本地 |
| [adr/003-petka-gui-explicit-only.md](./adr/003-petka-gui-explicit-only.md) | PETKA GUI 仅明示允许 |
| [adr/004-cms-rip-local-only.md](./adr/004-cms-rip-local-only.md) | CMS 抠模仅本机（App：引擎+底盘；不用 991 车身） |
| [cms-991-engine-rip.md](./cms-991-engine-rip.md) | CMS2021 Porsche DLC：991 引擎+底盘本机抠模 |
| [garage-3d-zone-pick.md](./garage-3d-zone-pick.md) | 车库 3D：分栏点穿点选（已实现，与代码同步） |

接棒先读 `requirements.md` + `../data/petka/README.md`，再跑 `npm run status:petka` / `status:cms-rip` / `status:mesh-map` / `accept:all`。  
3D 增强：`../data/seed/xray/README.md` · `../data/seed/flat-six/README.md`。

## 实时 OBD 文档入口

需求以 [requirements.md](requirements.md) R5.1/R6 为准，交付状态以 [progress.md](progress.md) 最新日期条目为准；历史方案不覆盖已确认要求。

- [使用说明](obd-live-guide.md)：设备匹配、读取故障、勾选分析、清码与车辆档案；当前能力和验证限制。
- [故障与数据分析细则](obd-fault-data-analysis-requirements.md)：已确认交互、8 项临时批次限制、收敛质疑及验收规则。
- [逐码数据需求 Markdown](research/obd-manual-data-needs.md)：统一 981/982 的 1,106 个索引代码，候选与缺口逐条列出。
- [数据生成说明](../data/seed/obd/README.md)：内部出处、候选提取、精简运行时数据及生成脚本。
- [架构](obd-architecture.md) 与 [分期沿革](obd-plan.md)：设计约束和历史计划；未实现的厂商诊断、独立设码仍保留为后续工作。
- [离线演练](obd-offline-guide.md)：无车模拟/回放，不作为实车成功证据。
- [981 设码参考数据](../data/seed/coding-guide/README.md) 与 [X431 无车取证](research/can-code/x431-dme-bench-2026-09-13.md)：参考资料与观测记录，不等同于已验证车辆协议。

当前没有实车验收证据。完整逐码检查流程、167 个尚无规范化对应的代码、厂商读取方法及 21 个索引外标题候选继续列为缺口。
