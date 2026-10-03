# 当前 OBD 使用与验收

更新：2026-10-03。产品仅 Windows；车辆操作以 [ADR 001](adr/001-no-ecu-write.md) 为准，结果和缺口见 [OBD_STATUS](../OBD_STATUS.md)。

## 四个入口

| 入口 | 使用方式 | 验证边界 |
|---|---|---|
| 系统拓扑 | 图/列表选择 ECU，查看模块名称与已读取的故障码；GW 显示批量读码/清码按钮；其他模块显示「实时数据 / 读取故障码 / 清除故障码」；实时数据跳转带入当前单元，未接入的读码/清码功能禁用 | 当前只有具名 981 DME/Gateway；VNCI 清码禁用，参考节点不等于本车已装配 |
| 连接设置 | 一行支持设备说明、合并的单选设备列表，以及刷新/连接设备/断开设备三个按钮；右上角显示当前已连接设备 | 选择不自动连接；切换设备先断开原连接；历史设备档案不代表当前在线；诊断期间互斥 |
| 实时数据 | 先选控制单元；DME 可切换标准 OBD / X431 清单，X431 自动使用唯一身份匹配版本，分类、分页、多选；开始后显示停止及另存为 | 标准六项仍走固定 `7DF/7E8`；X431 按钮当前只准备离线计划，尚无新实车数据 |
| 设码与编程 | 先选系统，再选维护/设码/特殊功能/编程，查看来源与离线准备；保存手工实测记录 | 981/982 资料不是本车适用性证明；无新增车辆写入通道 |

系统拓扑已移除顶部车型栏、参考网络统计与说明、重复能力说明、「982 参考」图例/文字/虚线标记，以及设码资料跳转和旧批量读取按钮。控制单元网格随可用宽度收缩，窄窗口按总线分段排列，不需要横向滚动。GW 详情保留模块名称，下方仅显示「读取所有单元故障码」和「清除所有单元故障码」两个按钮；两类操作点击即执行，当前可执行范围仍限 DME/GW，逐个处理，未接入单元不发请求；VNCI/PT3G 不开放批量清码。读取结果仍区分模拟、车辆与历史数据；DME 节点详情保留发动机实时数据入口。设码方案与记录通过「设码与编程」入口查看，X431 原值已核对不代表本项目可以写入。

拓扑页的设备说明卡片已移除。实时 OBD 右上角统一显示「已连接设备：vLinker」等当前连接名称；未连接、断开或连接失败时显示「已连接设备：无」。同一时间只选择和连接一个设备。连接设置保留关联设备的简短状态，移除步骤说明、详细设备卡片、额外跳转和清除选择按钮；断开后保留设备选择。

系统拓扑读取与清除故障码点击即执行，移除确认框和取消入口；仍保留互斥、失败/超时停止与结果保存。失败后的旧故障码标为“上次读取”，保留时间与模拟/车辆来源。清码继续核对具名 ECU 身份、保存清前记录、发送一次精确清码请求并复读；清码无法恢复。车辆通信时 X431 须退出诊断会话。快照保存失败提示及重试按钮已移除；未成功启动、没有采集事件 ID 的请求不生成诊断快照，实际采集结果仍自动保存。

实时数据按当前控制单元列出采集项，拓扑的「实时数据」按钮会带入该单元；打开页面、切换单元和勾选参数均不会启动通信。DME 清单为负荷、水温、转速、车速、进气温度、节气门。勾选项经 IPC 传至采集端，计划、支持位筛选、实际请求和导出均限于该选择；切换单元或选择会清空旧显示，采集期间锁定选择。曲线使用实际样本的时间和值，各参数独立纵轴，保留模拟/历史/中断标识；单个样本只显示一个点。

多选预算为 12 项，参考 [LAUNCH X-431 IMMO Pro 手册](https://launch-sa.com/wp-content/uploads/2025/04/X-431IMMOPROManual.pdf) 第 44 页分图界面（12 项；合并曲线为 4 项）。这是本页采用的分图显示预算，不表示所有 X431 型号或车辆菜单都只能采集 12 项。本地 X431 截图已核对多选清单、文字值及图形入口；当前 DME 六项均可选择，其他单元的 X431 参考项不会被冒充为独立可采集项。

## 收起页面后的工具

故障码独立页面及入口已移除；读取与清除故障码从系统拓扑执行，既有会话记录与故障台账保留。旧只读采集、离线工作台、广播记录、引导排障和维修对比页面已退役。后端、数据库记录、导出与 CLI 保留：

```powershell
python -m scripts.diagnostics --help
python -m scripts.diagnostics.sessions --help
python -m scripts.diagnostics.workbench --help
python -m scripts.diagnostics.can_monitor --help
python -m scripts.diagnostics.offline_match --help
node scripts/diagnostic-records.mjs --help
```

本地记录 CLI 必须显式指定现有项目 SQLite 文件；`snapshot:*`、`guide:*`、`compare:*` 经项目数据库 API 操作，不连接适配器。写入本地记录的操作必须提供 JSON 对象文件；`--help` 列出输入字段，完整校验见 [数据库 API](../packages/db/src/obd-store.ts)。输出是 JSON，可重定向为本地记录导出。

```powershell
node scripts/diagnostic-records.mjs --db .local/garage.db snapshot:list
node scripts/diagnostic-records.mjs --db .local/garage.db guide:list
node scripts/diagnostic-records.mjs --db .local/garage.db compare:list
```

读取单条记录时，把 `{"id": 1}` 写入本机 `input.json`，再执行 `node scripts/diagnostic-records.mjs --db .local/garage.db snapshot:get input.json`。维修对比先用 `compare:preview` 查看；未知身份不能直接对比，`snapshot:assign` 仅记录明确的人工归属声明，不能伪造观察到的 VIN。后读失败不会被视为故障已消失。引导步骤使用 `guide:get` 返回的步骤 ID 和 `updatedAt`，避免覆盖其他修改。

车辆现场步骤属于本机工作单 `docs/vehicle-connection-runbook.md`，含设备身份与实际授权记录，继续本地保存；这些帮助/记录命令不授权任何现场操作。

## X431 实时数据离线匹配

`scripts/diagnostics/offline_match.py` 直接读取本机 X431 变体、DSN 指针、已有抓包及具名身份记录，不枚举设备或访问 SQLite。运行时必须指定尚不存在的输出目录：

```powershell
python -X utf8 -m scripts.diagnostics.offline_match --output-dir .local/vehicle-analysis/981-offline-match-next
```

固定资料的 SHA-256 不符会停止；具名身份重新从原始串口响应解码，模拟或缓存成功不能通过资格核对。响应按适配器流、显式地址对、请求与抓包阶段分组；相同响应覆盖的参数保留共同来源，完全重复的参数另计合并数量。DSN 的有界字节遍历只缩小候选，不能替代完整 ECU 身份或原生选择器条件。

输出 `report.md`、`units.json`、`selected-variants.json`、全量 `parameters.jsonl`、身份与 DSN 证据、响应分组和哈希清单。当前输出只供离线研究，不改变桌面六项标准参数或车辆指令允许列表。结果计数见 [OBD_STATUS](../OBD_STATUS.md)。

## 实时数据离线准备与桌面清单

编译器 `scripts/diagnostics/realtime_preparation.py` 校验已有匹配包的全部输入/输出哈希，解析身份位置、展开参数名称引用、按变体去重、建立分组计划和历史回放。全部生成文件留在本机 `.local/diagnostics/realtime-preparation/`；首次可运行 `npm run prepare:obd-realtime`，已有输出时须指定新的输出目录，避免覆盖证据：

```powershell
python -X utf8 -m scripts.diagnostics.realtime_preparation --output-dir .local/diagnostics/realtime-preparation-next
python -X utf8 -m scripts.diagnostics.realtime_preparation_accept
```

桌面清单读取默认本机目录，可由主进程环境变量 `PORSCHE981_REALTIME_PREPARATION` 指定另一份已生成包。渲染进程只能传菜单单元、版本和最多 12 个参数 ID，不能指定文件、公式、端口或发送命令。查询验证索引与变体文件哈希，错误版本、单元或参数组合会拒绝；缺本机资料时显示尚未生成，不使用虚构种子值。

桌面只自动展示唯一身份匹配的本车版本，不再提供版本下拉菜单；当前为 DME/Gateway，其余单元的 DSN/系统名候选不自动作为本车版本。分类和列表查询过滤名称为零的占位行，真实数值为零的测量数据保留。计划合并相同读取请求并计算响应长度下限，附带身份字段、地址候选和逐版本接车清单，不推定采样频率或响应延迟。

“数据采集”当前执行 X431 离线计划准备，处理期间可停止并终止所属后台进程；完成后“保存此次采集”打开系统另存为窗口。JSON 记录版本、所选参数、时间及计划，明确 `vehicleDataCollected: false`，不伪装实车样本；取消另存为保留结果。标准 OBD 的停止中断采集任务，完成后同样以另存为保存本次结果及原有模拟/实车来源字段。桌面移除历史回放和离线导出按钮，后台历史回放接口仍保留。

设码与编程直接展示系统列表与四类功能，移除重复页头、搜索框、功能方案、操作记录和原始归档入口。78 条 X431 原始菜单与已有功能合并；保留菜单路径、年款与来源行号，对明确等价的维护功能及同系统唯一编程流程合并入口。通用重复操作模板不作为经验证的执行步骤，手工实测记录仍可在功能详情保存，既有数据库记录不删除。

X431 清单按源测量分组提供分类（例如 DME 的通用信息及其他分组），分类名来自同一中文 DSTREAM 字典，不按名称猜类别。重复参数保留多个来源分组，分类切换不丢失已勾选项。列表采用单行勾选框、名称、单位和记录状态，长名称悬停可看完整内容；列表内部纵向滚动，页面不因参数数量持续拉长。

当前 35 个菜单单元不代表本车安装数量；52,299 项覆盖所有 291 个版本。来源缺名称的 102 项显示请求和字节位置，PCM 版本后缀条件仍列为缺口。已有响应的 3,048 项跨变体计数不能代替具名身份关联的 DME 217 项 / Gateway 1 项。其他单元经 vLinker 独立采集前仍需核实本车完整身份和实际响应。

## 软件验收

- `npm run accept:obd-workspace`：生产构建、隔离 Electron 的四入口检查及设码/PIWIS 工作区验收。
- `npm run accept:obd-tools`：保留的本地记录 CLI、离线诊断与只读会话自检。
- `npm run accept:obd-transports`：连接、设备注册、VNCI 与广播工具自检。
- `npm run accept:obd-offline`：原模拟 worker、SQLite 持久化、取消与原始回放。
- `npm run accept:obd-realtime-offline`：离线准备单测、整个本机包的哈希/参数/计划核对、IPC 自检及真实 Electron 离线清单验收；需本机已生成准备包。

界面验收使用临时库、禁止 live，并为连接使用注入传输；不会把供电模拟或 Python simulation 当作实车结果。`accept:all` 只涵盖其编排的业务集合；全量 TypeScript 与实车缺口另行列明。
