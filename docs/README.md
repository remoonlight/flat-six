# 项目文档入口

更新：2026-10-05。产品是 2014 Boxster S（981）PDK 的本地 Windows Electron 车库；981/982 资料目录合并展示，实车适用性按车辆与 ECU 单独判断。

## 先读这四处

| 入口 | 唯一职责 |
|---|---|
| [requirements.md](requirements.md) | 产品需求与已确认范围；专题文档补充细节 |
| [adr/](adr/) | 车辆操作、平台、PETKA GUI 与 CMS 资产边界 |
| [progress.md](progress.md) | 当前交付状态和未闭合缺口；链接到详细证据 |
| [OBD_STATUS.md](../OBD_STATUS.md) | OBD 当前结果、实车/离线验证区别和下次验收 |

当前用户指令优先于项目文档；当前 ADR 决定优先于历史阶段描述。需求描述目标，代码说明实现，验收产物说明实际验证范围，三者不能互相替代。历史工作单不继承授权。Codex 直接执行；Cursor CLI、Grok 与未获当次委派授权的其他 worker 不作为执行器。

状态只按当前工作树和注明日期/范围的证据解释；工作树中的文档修改不代表已经提交或发布。全项目概览由 progress 维护，OBD 计数与验证由 OBD_STATUS 维护，当前步骤由 obd-current-guide 维护。旧架构、故障页设计及路线图属于历史来源端快照，不将旧按钮、8 项预算、Mode 04 或设备状态继承为当前要求。完整维修步骤和 981/982 覆盖目标继续保留，不因删除页面或清理过程文件消失；本轮核对与未闭合的实现/验收见 [收敛质疑](convergence-review.md#本轮收敛质疑)。

2026-10-05 新增的不接车软件交付、验证范围和未具备的车辆资格统一见 [当前进度](progress.md#2026-10-05-不接车的软件交付)；普通结果默认不保存、编码备份强制保留、恢复只处理当前单元等已确认规则保持。

## 当前使用

[四个 OBD 入口、保留的 CLI 与软件验收](obd-current-guide.md)，以及 [接车资料分批采集清单](obd-current-guide.md#接车资料采集先保留原始数据断开后再梳理)。核心文档可用 `npm run accept:docs` 检查隐私、白名单和可提交文件链接。

## 按任务查阅（本机专题按需提供）

| 任务 | 文档与数据 |
|---|---|
| 启动与开发 | [根 README](../README.md)、[Agent map](../AGENTS.md) |
| 车辆只读接入 | Codex 接车工作单（本机资料：`docs/vehicle-connection-runbook.md`）、CLI（本机资料：`docs/diagnostics-cli.md`）、拓扑与 DTC（本机资料：`docs/topology-dtc-workflow.md`） |
| 设备证据 | 本机清单（本机资料：`docs/device-inventory.md`）、MX+（本机资料：`docs/obdlink-mxplus.md`）、VNCI（本机资料：`docs/vnci-usb.md`）、PT3G（本机资料：`docs/pt3g-driver.md`）；安装记录不代表当前在线 |
| 标准发动机与现场批次 | 标准路径现场记录（本机资料：`docs/standard-engine-field.md`）、2026-10-01 收尾快照（本机资料：`docs/obd-precar-20261001.md`）；当前桌面状态以 OBD_STATUS 为准 |
| 设码与 PIWIS | 就绪度（本机资料：`docs/hidden-feature-readiness.md`）、981/982 工作区（本机资料：`docs/piwis-workshop.md`）、991 文档预留（本机资料：`docs/piwis-991-interface.md`）、[设码菜单](../data/seed/coding-guide/README.md)、原件归档（本机资料：`data/seed/piwis/archive/README.md`） |
| 零件与定位 | [PETKA](../data/petka/README.md)、[公开目录](../data/seed/parts/catalog/README.md)、[间隔](../data/seed/intervals/README.md)、3D 点选（本机资料：`docs/garage-3d-zone-pick.md`）、模型下钻（本机资料：`docs/garage-parts-model-drilldown.md`） |
| 3D 资产管线 | [X-ray](../data/seed/xray/README.md)、[flat-six](../data/seed/flat-six/README.md)、[PETKA 模型](../data/seed/petka-models/README.md)、CMS 本机抠模（本机资料：`docs/cms-991-engine-rip.md`） |
| 协议与定义研究 | 独立诊断研究（本机资料：`docs/independent-diagnostics.md`）、公开 CAN 资料（本机资料：`docs/research/can-data/README.md`）、候选索引（本机资料：`docs/981-evidence-index.md`）、X431 离线解码（本机资料：`docs/research/can-code/x431-offline-decoding-2026-09-27.md`）、离线演练（本机资料：`docs/research/can-code/x431-offline-rehearsal-2026-09-27.md`）、[逐码数据需求](research/obd-manual-data-needs.md) |
| 文件归属、清理结果与未决问题 | [文件收敛](convergence-review.md) |

## 历史与设计资料

- 交付历史（本机资料：`docs/archive/progress-through-2026-10-02.md`）：保留原进度正文、日期、计数和 Q 项结论。
- 本轮替换前 OBD 汇总（本机资料：`docs/archive/obd-status-before-audit-2026-10-04.md`）：保留旧页面与测试细节，当前汇总不再混写旧版状态。
- 来源端迁入记录（本机资料：`docs/archive/legacy-desktop-docs-2026-09-27/MIGRATION.md`）：来源环境和未迁入依赖；不作为本机验收。
- 2026-09-13 文档交接仅迁入来源端资料，完整代码、运行数据和验收证据未随包迁入；当时的版本冲突与缺项报告保留在本机 `.local/handoff-20260913/IMPORT-REPORT.md`，旧授权不继承。
- [交互设计](obd-fault-data-analysis-requirements.md)、[架构](obd-architecture.md)、[路线图](research/can-code/981-live-obd-and-coding-roadmap.md) 保留仍有用途的设计依据；当前操作统一查 [当前使用](obd-current-guide.md)。两份过期使用说明、旧分期计划及 Cursor 工作单跳转文件已删除；迁入原文保留来源环境和版本差异。

## 文档与文件归属

`docs/` 采用核心白名单：需求、ADR、当前状态、总入口、当前使用说明和收敛审查纳入版本管理；设备清单、原件与私人历史报告继续本地保存。下方标为“本机资料”的路径可能不随 clone/ZIP 提供；缺失不影响核心边界和四入口使用说明。

`data/seed/` 的 README 与源数据同放，研究原件、派生数据和运行数据库按各自管线保留。本轮不合并车型原始手册，不删除捕获或固件归档，不用 Markdown 摘要替代原件。
