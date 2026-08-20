# 991 — OBD / 故障相关（中文归档）

> 车型：911（991）Carrera / S / GTS / Turbo 等及敞篷/Targa/GT3 车间册  
> 来源：  
> 1. `data/991_Carrera CarreraS CarreraGTS Turbo TurboS.pdf`（车间维修手册）  
> 2. `data/991_CarreraCabriolet TurboCabriolet TargaGTS GT3.pdf`（车间维修手册，同族步骤）  
> 3. `data/991_Factory Service Manual.pdf`（英文厂服，**核对用**；正文归档为中文意译）

敞篷册与轿跑册在「车载诊断故障记忆 / 车辆移交」上步骤同构；下列以轿跑册页码为主，敞篷册页码约少 5～10 页（同标题可检索）。

---

## 1. 车载诊断系统的故障记忆（WM 033500）

**出处：** 轿跑册抽取页 84–85（敞篷册对应约 79–80）。

### 电压注意

电压降低可能导致：控制单元严重损坏、故障条目、编码中止、编程中故障等。

- 断开控制单元前：关闭点火并取下钥匙。  
- 编程过程电源不可断。  
- 蓄电池充电器 ≥ **40 A**。

### 连接

经**诊断插座**连接 **PIWIS 检测仪 II（9818）**；插座在**车内左侧保险丝盒下方**。

### 步骤（菜单英文原文意译）

1. 启动 PIWIS II。  
2. 选车型并启动 PIDT。  
3. 控制单元概述 → F7。  
4. 「读取所有故障记忆，必要时清除」→ 确认。  
5–7. 检查 / 删除或保留故障 → 返回。

**英意核对：** 与 981 同名 WM 033500 英文菜单一致（*Read out all fault memories and erase them if necessary*）。

---

## 2. 诊断系统：车辆移交

**出处：** 抽取页 46–47。

移交流程包含读取全部故障记忆并必要时清除；试驾后再次读取；移交前按需检查并清除。

---

## 3. 技术数据中的 OBD 法规标注

**出处：** 轿跑车间册技术数据（抽取页 131）；英文厂服同类页（如抽取 4704、4707）。

中文车间表述示例：

- **USA LEV II 版** → **车载诊断 II（OBDII）**  
- **Euro 5 / 欧 Ⅳ** 等 → **欧洲车载诊断（EOBD）**

**英意核对（厂服原文）：**

- *On-Board Diagnosis II (OBDII)* / *Low Emission Vehicle II (LEV II)*  
- *Euro-On Board Diagnosis (EOBD)*  

归档采用：车载诊断 II（OBDII）、欧洲车载诊断（EOBD）。此为**排放法规诊断等级**标注，不是故障码列表。

厂服另有大量 *on-board diagnosis* / *diagnostic socket* 出现在后桥转向标定、维护后读码等工序（例如抽取 6535、7029）：语义均为「经诊断插座用 PIWIS 做车载诊断」，不逐条抄录。

---

## 4. 与驾驶警示灯的关系

991 本仓 PDF 以车间手册为主，**未**附与 981 同版式的完整中文驾驶手册 Check Engine 专章。仪表「排放控制警示灯 / Check Engine」口径参见：

- [`../981/obd-faults.md`](../981/obd-faults.md) §1（同代产品语言）  
- 英文厂服 *malfunction indicator* 命中页（如 5565）对应「故障指示」类表述，用于核对术语而非替代驾驶手册全文。

---

## 5. 示例：故障指示相关车间条目

防盗报警检查（抽取页 41）等流程要求：异常时连接 PIWIS **读取故障记忆**（「故障指示灯」出现在工序标题语境，非 MIL 专章）。

具体 P 码词典仍非本手册范围；App 查码种子见 `data/seed/dtc/bootstrap.json`。

---

## 6. 英意核对要点

| 中文归档 | 英文厂服 / 菜单 | 说明 |
|----------|-----------------|------|
| 车载诊断 II（OBDII） | On-Board Diagnosis II (OBDII) | 技术数据 |
| 欧洲车载诊断（EOBD） | Euro-On Board Diagnosis (EOBD) | 技术数据 |
| 诊断插座 | diagnostic socket | 位置：左侧保险丝盒下 |
| 故障记忆 | fault memories | 读/清菜单 |
| 故障指示（灯） | malfunction indicator | 厂服/工序用语 |
