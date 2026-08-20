# ID.3 / ODIS / SFD / SFD2 综合整理

> 合并自三份文档 · 2026-07-11  
> VIN：`LSVZZZ00ZES000012`（演示号 · 上汽大众 ID.3 · 年款码 S ≈ 2025）  
> 来源：本机 ODIS 安装分析 + 全车扫描 + 改装 DID 粗估

---

# 第一部分：ODIS / SFD / SFD2 知识点

> 适用机型示例：上汽大众 ID.3 2025（本机日志项目号 `VWE31CN`，中国市场 WMI 常见 `LSV`）  
> 来源：本机 ODIS 安装分析 + 协议 WSDL + 公开资料

## 1. 本机软件环境

| 软件 | 路径 | 说明 |
|------|------|------|
| ODIS Engineering 19 | `C:\Program Files\OE` | 已安装 |
| ODIS Engineering 17 | `C:\Program Files\OE17` | 曾运行 `OffboardDiagLauncher_protected` |
| ODIS Service 25 | `C:\Program Files\OS` | 插件最全 |
| Token 落盘目录 | `C:\ProgramData\OE\SFD_Token`、`C:\ProgramData\OE17\SFD_Token` | 可为空 |
| Token 服务 JAR | `...\configuration\org.eclipse.osgi\...\lib\ECUDiagnosticTokenService.jar` | **明文 WSDL**，可读 |

## 2. 为什么磁盘上抠不出“加密算法”

- SFD/SFD2 业务实现类在 `zecret` 包中，`.class` **不是**标准 `CAFEBABE`，为加密载荷。
- 客户端本地**没有**可用于伪造签名的私钥。
- WSDL 写明：Token 由 **service provider（大众后端）签名**。

相关主要 JAR（OS）：

- `de.volkswagen.odis.vaudas.business.selfdiag_*.jar` — SFD / SFD2 业务
- `de.volkswagen.odis.vaudas.diag.zecret_*.jar` — 解锁 UDS 命令
- `de.volkswagen.odis.vaudas.infoservices_*.jar` — Token 服务封装
- `ECUDiagnosticTokenService.jar` — 明文 SOAP 协议定义

关键类名线索：

- SFD：`SecurityAccessSFD*`、`SfdUnlockEcuCommand`、`DiagProtection*Unlock*`
- SFD2 / E2E：`DatasetUdsWithSFD2Writer`、`SecurityEndToEndTokenManager`、`GetSecurityToken`
- 品牌差异：`SecurityAccessSfdBrandAdjuster`

## 3. SFD vs SFD2

| | SFD（v1） | SFD2（v2） |
|--|-----------|------------|
| 保护对象 | 诊断访问 / 模块级解锁窗口 | 编码、适配、参数化等**写数据** |
| 粒度 | 常为模块限时解锁（如约 90 分钟） | 更细：常绑定具体数据/DID，一次性 |
| Token 特点 | 解锁后窗口内可操作 | Token 含待写数据校验（如 checksum/hash），**一次一票** |
| 典型用途 | 进入受保护诊断会话 | 真正改写 coding / adaptation / dataset |

2024+ 多数新车（含 ID.3 MY2025）常见：**SFD1 + SFD2 叠加**。只解 SFD1 不够写受保护通道。

## 4. 授权流程（官方路径）

### 4.1 SFD（诊断解锁）

```
ECU ──UnlockRequestStructure──→ ODIS
         │
         ▼
  技师账号登录（GeKo / 经销商门户等）
         │
         ▼
  ProcessECUTokenRequest / ProcessVINTokenRequest
  （SOAP: ECUDiagnosticTokenService）
         │  必填：VIN、角色、时长、诊断地址、请求结构
         ▼
  大众后端校验权限并签发 Token
         │
         ▼
ODIS ──Unlock──→ ECU（限时开放）
```

`ProcessECUTokenRequest` 主要字段：

- `VehicleRef.VIN`（强制）
- `ECURequestStructure`（ECU 挑战/请求结构）
- `ECURole`：`Basic | Extended | SuperUser`
- `DurationOfActivation`：`0x01 | 0x02 | 0x03`
- `DiagnosticAddress`
- 响应：`Token`（后端已签名二进制）

### 4.2 SFD2（写数据 / End-to-End）

```
准备待写数据 → 客户端生成请求（含哈希/校验）
       → GetSecurityToken / GetVINSecurityToken（强制 VIN）
       → 后端签发 AdjustmentSecurityToken / DataSecurityToken
       → 随写操作下发 ECU
```

无有效 SFD2 Token 则 ECU 拒绝写入。

## 5. 跨账号能否授权？（上汽大众车）

**结论：一汽大众或欧洲经销商账号，一般不能给上汽大众（SVW）中国车发有效 Token。**

| 账号类型 | 对 SVW ID.3 | 原因 |
|----------|-------------|------|
| 上汽大众正规经销商/授权技师 | 正常路径 | VIN/市场与组织匹配 |
| 一汽大众经销商 | 基本不行 | 不同合资体系 entitlement |
| 欧洲本部/欧系经销商 | 基本不行 | 市场/进口商不同 |
| 集团工程/特殊支持 | 极少数例外 | 非门店常规权限 |

后端校验的是：**账号所属组织是否有权对该 VIN 签发 Token**，不是“同属大众集团即可”。

本机日志线索：`VWE31CN`、`wmi[LSV]`、`brand(s) for project (VWE31CN) => [VW]`。

## 6. 明文 Token 服务操作一览

零售服务 WSDL：`ECUDiagnosticTokenService_V1.wsdl`  
逻辑地址示例：`ws://volkswagenag.com/Retail/ECUDiagnosticTokenService/V1`

| 操作 | 用途 |
|------|------|
| `ProcessECUTokenRequest` | 按 ECU 解锁 Token（偏 SFD） |
| `ProcessVINTokenRequest` | 按 VIN 批量地址 Token |
| `GetSecurityToken` | 适配/应用数据 Security Token（偏 SFD2） |
| `GetVINSecurityToken` | VIN 级 Security Token |
| `ProcessAliveTest` | 存活检测 |

开发/HIL 另有 `DEVECUDiagnosticTokenService`、`HILECUDiagnosticTokenService` 等，面向工程/台架，非零售店常规。

## 7. 上汽不给 SFD2 权限时：网上常见说法（仅作信息汇总）

> 以下为公开网页上的讨论与商业服务介绍，**不等于推荐或保证可用**；中国 SVW 车是否适用需自行核实。第三方 Token 服务存在合规、账号与车辆数据安全风险。

### 7.1 官方/合规方向

- 仍须具备 **SFD 后端授权账号**（经销商门户 / Group Retail Portal 角色）。
- OBDeleven 等表示 SFD2 依赖与大众集团授权合作，尚未简单对终端用户全面开放。
- 官方条款：Token 与个人账号绑定并记日志，禁止随意转交第三方。

### 7.2 工具侧公开说明（多为欧洲/通用 VAG）

| 来源 | 要点 |
|------|------|
| [VCTool SFD 说明](https://vctool.app/sfd/) | 可准备 SFD2 待签名数据（如 JSON），**本身不签名**；需另找 Token Provider |
| [vagupdate SFD2 指南](https://vagupdate.com/blog/general/sfd2-unece-protection-guide) | 描述 SFD2 为 UNECE 网络安全要求下的写保护；提供商业解锁服务宣传 |
| Autel / VXDIAG 等博客 | 多讲 **SFD1** 在线/手动解锁与购 Token；不等同已解决全部 **SFD2** |
| [Hackaday 讨论](https://hackaday.io/project/202595/log/240838-sfd2-2-steps-forward-1-step-back) | 反映 SFD2 第三方签名昂贵、渠道稀缺 |
| 消费权益类条目 | 指出 SFD/SFD2 强化经销商依赖，独立维修/车主改参受限 |

### 7.3 现实判断（针对“上汽不给权限”）

1. **正规店拒开 SFD2**：常见原因是政策、角色权限、操作类型（非保修/非原厂作业）、账号未开 SFD2 角色。
2. **换一汽/欧洲账号**：对 SVW VIN 通常无效（见第 5 节）。
3. **第三方 Token 服务**：网上有“提交 challenge/JSON → 回签 Token”的商业服务，多面向欧规工具链；对中国上汽车 **成功率与合法性不确定**，且需把车辆请求数据交给第三方。
4. **只读诊断**：多数情况下读故障/数据流仍可不解锁 SFD2；卡点在 **写 coding/adaptation**。

## 8. 建议排查顺序（合规优先）

1. 确认需求是 **SFD1** 还是 **SFD2**（只读 vs 写参）。
2. 找 **上汽大众** 有 SFD 角色的店/技师，问清是否开通 **SFD2 / 数据写保护** 权限，而非仅诊断解锁。
3. 核对账号角色：Basic / Extended / SuperUser，以及门户里 SFD 标准角色是否分配。
4. 若为保修/召回/原厂刷写：要求店端用官方在线流程并保留日志。
5. 非官方第三方签名：自行评估合规与数据安全；本文不提供伪造或绕过指导。

## 9. 参考路径（本机）

- WSDL 解压参考：`%TEMP%\odis_sfd_extract\token_svc\META-INF\wsdl\`
- 日志：`C:\ProgramData\OE\log\engine.log`、`C:\ProgramData\OE17\log\engine.log`

*第一部分仅作技术梳理与公开信息汇总，不构成对破解、伪造 Token 或违规使用他人经销商账号的建议。*

---

# 第二部分：全车 SFD 排查报告

- **VIN**: `LSVZZZ00ZES000012`（演示号 · 上汽大众 ID.3，年款码 S≈2025）
- **工具**: ODIS Service 25.0.3
- **VCI**: MongoosePro JLR（日志：不能做 DoIP Bridge）
- **更新**: 2026-07-11 02:25
- **临时文件目录**: 本机临时目录（勿把绝对路径提交进仓）

## 结论摘要

| 项目 | 结果 |
|------|------|
| 已确认 SFD 锁定 | **5** 个（`setSfdStatus` → error `1` = 未解锁） |
| 已识别控制单元 | **35** |
| 未识别 UNKNOWN | **约 45** |
| SFD1 | **未发现**（日志无 SFD1/SFD2 字面字段） |
| SFD 类型推断 | **SFD2**（MEB 2025 平台） |
| 正规解锁 | 无官方 token（`Login required`），不可用 |

## 已确认带 SFD 锁定的模块

来源：`engine.log*` 中 `SecurityAccessSFDUnlockUtil.setSfdStatus`，操作多为 `DiagnServi_ReadDataByIdentECUIdent` / `MeasuValue`。

| 地址 | 日志名 | 中文 | HW零件号 | SW零件号 | HW版本 | SW版本 | 类型 | 状态 |
|------|--------|------|----------|----------|--------|--------|------|------|
| `0x0001` | EnginContrModul1 | 驱动电机控制单元1 | `0EA907425L` | `0EA906014GQ` | H80 | 4466 | SFD2(推断) | 未解锁 |
| `0x0019` | Gatew | 网关/诊断接口 | `1EE937012` | `1EE937012G` | 032 | 0583 | SFD2(推断) | 未解锁 |
| `0x0075` | TelemCommuUnit | 远程通信单元 | `14G035284` | `14G035284C` | H23 | 0618 | SFD2(推断) | 未解锁 |
| `0x008C` | BatteEnergContrModul | 高压电池能量管理 | `5KE915184AB` | `5KE915184BL` | 005 | 1152 | SFD2(推断) | 未解锁 |
| `0x00A5` | FrontSensoDriveAssisSyste | 前部驾驶辅助传感器 | `1EA980653A` | `1ED980653F` | H08 | 5570 | SFD2(推断) | 未解锁 |

## 已识别模块零件号一览

来源优先级：`engine.log` 的 `hwpnr`/`swpnr` → 诊断协议解码 `dprot_decoded.txt` → 界面识别结果。软件集群类多为软件版本号（`V04…`），无传统硬件零件号。

| 地址 | 名称 | 中文 | HW零件号 | SW零件号 | HW版本 | SW版本 | SFD |
|------|------|------|----------|----------|--------|--------|-----|
| `0x0001` | EnginContrModul1 | 驱动电机控制单元1 | `0EA907425L` | `0EA906014GQ` | H80 | 4466 | 已确认锁定 |
| `0x0003` | Brake1 | ABS/ESP 制动 | `1EA614517M` | `1EA907379BR` | H18 | 0757 | 待确认 |
| `0x0008` | AirCondi | 空调/暖风 | `1EG907007B` | `1EG907007D` | H01 | 0263 | 待确认 |
| `0x0009` | CentrElect | 中央电气 | `1EE937089` | `1EE937089K` | H08 | 0562 | 待确认 |
| `0x0013` | AdaptCruisContr | 自适应巡航 | `1EA907819` | `1EA907567D` | H05 | 0250 | 待确认 |
| `0x0015` | Airba | 安全气囊 | `3WD959655J` | `3WD959655J` | 006 | 0150 | 待确认 |
| `0x0016` | SteerColumElect | 转向柱电子 | `1EE953507CB` | `1EE953507CB` | 005 | 0067 | 待确认 |
| `0x0019` | Gatew | 网关/诊断接口 | `1EE937012` | `1EE937012G` | 032 | 0583 | 已确认锁定 |
| `0x0023` | BrakeBoost | 制动助力 | `1EA909059Q` | `1EA909059BG` | H33 | 0853 | 待确认 |
| `0x003C` | LaneChangAssis | 变道辅助 | `2QD907686E` | `2QD907686E` | H26 | 0320 | 待确认 |
| `0x0042` | DoorElectDriveSide | 驾驶员车门电子 | `1EB959593A` | `1EB959593C` | 006 | 0655 | 待确认 |
| `0x0044` | SteerAssis | 转向助力 | `1EG907144E` | `1EG907144J` | 162 | 5122 | 待确认 |
| `0x0051` | DriveMotorContrModul | 驱动电机控制 | `1ED907230` | `1ED907121CJ` | H01 | 5201 | 待确认 |
| `0x0052` | DoorElectPasseSide | 副驾驶车门电子 | `1EB959592A` | `1EB959592C` | 006 | 0655 | 待确认 |
| `0x005F` | InforContrUnit1 | 信息娱乐主机 | `10C035878` | `10C035878B` | H08 | 8704 | 待确认 |
| `0x006C` | CamerSysteRearView | 倒车影像 | `5WD907556A` | `1EG907556C` | H35 | 0685 | 待确认 |
| `0x0075` | TelemCommuUnit | 远程通信单元 | `14G035284` | `14G035284C` | H23 | 0618 | 已确认锁定 |
| `0x0076` | ParkiAssis | 泊车辅助 | `1EE919300` | `1EE919300B` | H01 | 0630 | 待确认 |
| `0x007E` | DashBoardDisplUnit | 仪表显示 | `11D920320` | `11D920320A` | 301 | 3720 | 待确认 |
| `0x0082` | HeadUpDispl | 抬头显示 | `10D919597` | `10D919597` | H05 | 2502 | 待确认 |
| `0x008C` | BatteEnergContrModul | 高压电池能量管理 | `5KE915184AB` | `5KE915184BL` | 005 | 1152 | 已确认锁定 |
| `0x00A5` | FrontSensoDriveAssisSyste | 前部驾驶辅助传感器 | `1EA980653A` | `1ED980653F` | H08 | 5570 | 已确认锁定 |
| `0x00B7` | AccesStartInter | 进入启动系统 | `5WA959436N` | `5WA959436AD` | 122 | 0771 | 待确认 |
| `0x00C0` | ActuaForExterNoise | 外部声浪执行器 | `1EA035335` | `10D035335C` | H06 | 0007 | 待确认 |
| `0x00C6` | BatteChargContrModul | 充电控制 | `1EG915681E` | `1EG915681H` | H65 | 1124 | 待确认 |
| `0x00CA` | ContrModulForSunro | 天窗控制 | `4K0907594` | `4K8907594AD` | 010 | 0010 | 待确认 |
| `0x00D6` | LightContrLeft2 | 左大灯控制2 | `992941572A` | `992941572AG` | H17 | 9104 | 待确认 |
| `0x00D7` | LightContrRight2 | 右大灯控制2 | — | — | — | — | 待确认 |
| `0x8105` | DCDCConveContrModul12V | 12V DCDC | `1ED907190G` | `1ED907190K` | H72 | 1355 | 待确认 |
| `0x8107` | AntenContrModul | 天线控制 | `1EA035741F` | `1EA035741R` | H05 | 0559 | 待确认 |
| `0x8123` | AppliServe1Syste1Adapt | 应用服务器1自适应 | — | `V04007006L` | — | 0583 | 待确认 |
| `0x8124` | AppliServe1Syste2Java | 应用服务器1 Java | — | `V04007006M` | — | 0583 | 待确认 |
| `0x8125` | AppliServe3Syste1Infot | 应用服务器3信息娱乐 | — | `V04007000TD` | — | 8704 | 待确认 |
| `0xC002` | SoftwClustEmbed1 | 软件集群嵌入1 | — | `V04007006N` | — | 0583 | 待确认 |
| `0xC003` | SoftwClustHouse1 | 软件集群管家1 | — | `V04007006P` | — | 0583 | 待确认 |

### 网关相关附加识别（协议/数据集）

| 项 | 值 |
|----|-----|
| 网关数据集 | `EV_GatewICAS1MEBUNECE` |
| 相关零件（协议中出现） | `1EA915181C`、`3JG959542A` |

## 本轮操作记录

1. 已进入诊断会话；自诊断可对网关等做「识别」。
2. 对 `Gatew` / `SoftwClustEmbed1` / `SoftwClustHouse1` 执行过 `SinglJob_StandECUIdent`，**未新增** `setSfdStatus`（与早期 ReadDataByIdent 路径不同）。
3. 控制单元列表可见约 80 条；`0003` 制动等可选中。
4. **卡点（当前）**：弹出「结束诊断 / 是否要结束已有的诊断会话？」模态框，挡住列表双击与底部「诊断」；连接标记常为 `marked=false`；Mongoose 无 DoIP Bridge。

## 下一步

1. 弹窗点 **「否」**，保留当前诊断会话。
2. 在「控制单元列表」选中目标模块 → 右侧切 **「自诊断」**，或底部「诊断」可用时再进。
3. 进模块后功能下拉选 **「识别」** → 绿箭头执行 → 扫日志补全 SFD 表。

## 相关脚本与数据（均在 D:\aaa）

- `parse_sfd2.ps1` — 汇总 `setSfdStatus` 按 ECU
- `dump_ecu.ps1` — 导出 IDENTIFIED/UNKNOWN
- `batch_ident.ps1` / `batch_access.ps1` — 批量识别/访问权限尝试
- `ecu_partnumbers.txt` — 日志提取的 `hwpnr`/`swpnr`
- `dprot_decoded.txt` — 诊断协议解码零件号

## 控制单元列表快照（界面可见，含事件数）

诊断模式「控制单元列表」约 **80** 条记录。红字=有事件。

| 地址 | 事件 | 名称（界面） | 界面可见零件号/版本 |
|------|------|--------------|---------------------|
| 0001 | 3 | 发动机电控系统 | `0EA906014GQ` / SW 4466 |
| 0003 | 7 | 制动电子装置 | `1EA907379BR` / 0757 ESC-ZF |
| 0008 | 0 | 空调/暖风电子装置 | `1EG907007D` / 0263 Climatronic |
| 0009 | 3 | 电子式中央电气系统 | `1EE937089K` / 0562 SAM_H |
| 0013 | 0 | 自适应巡航 | `1EA907567D` / 0250 ARS512VW13 |
| 0015 | 4 | 安全气囊 | `3WD959655J` / 0150 Airbag VW50 |
| 0016 | 0 | 转向柱电子装置 | `1EE953507CB` / 0067 |
| 0019 | 12 | 数据总线诊断接口（网关） | `1EE937012G` / SW 0583 / HW 032 |
| 0023 | 0 | 制动助力器 | `1EA909059BG` / 0853 EBB-ZF-Gen1 |

> 事件数≠SFD；SFD 仍以日志 `setSfdStatus` 为准（目前仅 5 个已确认）。

## 操作卡点（已记录）

1. 弹窗「结束诊断？」会挡住列表操作 → 应点 **否**。
2. 诊断模式下双击/底部「诊断」常无效（按钮带红叉）。
3. 误点右侧「测量技术」会进示波器，需再点回「诊断」。
4. 进入单模块功能：优先右侧 **「自诊断」**（选中目标行后点）。

---

# 第三部分：改装写参 DID 粗估

> 基于全车扫描（第二部分）· 2026-07-11  
> 说明：扫描报告**不含**具体 DID 号；下表为按模块与常见改法推算的**量级**，实车以 VCtool/ODIS 适配采集为准。  
> Token 参考：vagupdate SFD2 单次请求约 **≤10 个 DID**。

## 1. 扫描结论摘要

| 项目 | 结果 |
|------|------|
| 已确认 SFD 锁定模块 | **5** 个：`0x0001` / `0x0019` / `0x0075` / `0x008C` / `0x00A5` |
| 已识别控制单元 | **35** |
| SFD 形态判断 | **SFD2**（MEB 2025） |
| SFD1 | 未单独标明 |
| 官方 Token | 未登录（`Login required`） |

## 2. 目标功能 ↔ 车内相关模块

| 功能 | 文档中可能相关地址 | 扫描里是否具备 |
|------|--------------------|----------------|
| 右侧倒车下翻 | `0x0052` 副驾门控、`0x0009` 中央电气；记忆联动时可能 `0x0042` | 模块有；不在已确认 5 锁内 |
| 座椅记忆 | 座椅控制模块 + 门控镜像位置 + 可能网关 | **未见独立座椅记忆模块** |
| 加装哈曼卡顿 | `0x005F` 信息娱乐、`0x0019` 网关 + 功放地址 | 有主机/网关；**未见功放** |
| 灯光精灵（IQ.） | `0x0009`、`0x005F`；外灯相关 `0x00D6`/`0x00D7`；必要时 `0x0019` | 模块有；完整灯效依赖灯带硬件 |

> 写参时是否触发 SFD2 以实车为准；门控/中央电气/主机虽未进「已确认 5 锁」，仍可能受保护。

## 3. DID / 通道粗估

| 功能 | 粗估 DID/通道数 | 主要 ECU | 备注 |
|------|-----------------|----------|------|
| 右侧倒车下翻 | **2～5** | `0052` + `0009` | 只开功能偏少；绑座椅记忆位会略增 |
| 座椅记忆 | **3～8**（有硬件时） | 座椅模块 + 门控 | 扫描缺座椅模块 → 多半先加硬件 |
| 加装哈曼卡顿 | **5～15** | `0019` + `005F` + 功放 | 缺功放 → 先装再编；常需拆成多次 Token |
| 灯光精灵 | **2～6** | `0009` + `005F`（必要时 `00D6`） | 开菜单/场景常见；无灯带则编码也出不了完整灯效 |

### 合计（假设硬件齐全、仅写参）

| 口径 | 数量 |
|------|------|
| DID/通道合计 | **约 12～30** |
| 按每次 ≤10 DID 计 Token 次数 | **约 2～4 次** |

若只做「已有硬件、开功能」：

| 组合 | 粗估 DID | Token 次数（≤10/次） |
|------|----------|----------------------|
| 仅倒车下翻 | 2～5 | 1 |
| 倒车下翻 + 灯光精灵 | 4～11 | 1～2 |
| 上两项 + 座椅记忆（有模块） | 7～19 | 1～2 |
| 再加哈曼卡顿 | 12～30 | 2～4 |

## 4. 建议采集 / 施工顺序

1. **确认硬件**：座椅记忆开关/模块、HK 功放与喇叭、灯光精灵灯带是否已装。
2. **先采再买 Token**：在目标模块用 VCtool「Collect channel」导出待签数据，按 ≤10 DID 分包。
3. **推荐顺序**
   1. 右侧倒车下翻（`0052` / `0009`）
   2. 灯光精灵（`0009` / `005F`）
   3. 座椅记忆（先确认有座椅模块）
   4. 哈曼卡顿（先装功放，再 `0019` / `005F` / 功放）
4. **SFD**：网关 `0019` 若需写安装列表，注意与 SFD1/SFD2 解锁顺序（先 SFD1 开网关再采 SFD2 数据，以所用工具说明为准）。

## 5. 明确限制

- 全车扫描**不能**给出精确 DID 列表或精确个数。
- 本文件仅为整理与量级估算，**非**官方编码步骤，亦非破解/伪造 Token 指导。
- 上汽大众车第三方 Token 成功率与合规性需自行核实。

---

*合并整理时间：2026-07-11*
