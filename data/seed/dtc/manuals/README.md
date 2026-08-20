# 手册 OBD / 故障相关归档（中文）

> 本目录是**手册原文摘录的中文归档**，≠ App `dtc_kb`（`../bootstrap.json`）。  
> 入库查码仍走 bootstrap；此处供对照手册口径与术语。

## 车型目录

| 目录 | 主要来源 PDF（`data/`） | 交付 |
|------|-------------------------|------|
| [`981/`](./981/) | `981_Boxster BoxsterS BoxsterGTS.pdf`（车间维修手册，中英标题）；`981_Boxster, Boxster S, Boxster GTS 驾驶手册 (0814).pdf` | [`obd-faults.md`](./981/obd-faults.md) |
| [`982/`](./982/) | `982_Boxster BoxsterS BoxsterGTS.pdf`（合集末附车主手册中文段）；`982_GT4 Owners Manual.pdf`（英，仅核对） | [`obd-faults.md`](./982/obd-faults.md) |
| [`991/`](./991/) | `991_Carrera …pdf` / `991_CarreraCabriolet …pdf`（车间维修手册）；`991_Factory Service Manual.pdf`（英厂服，核对用） | [`obd-faults.md`](./991/obd-faults.md) |

跳过：`982_Cayman Navigation Retrofit.pdf`（导航改装，无 OBD 故障专章）。

## 按 code 汇总

分车型并行抽取后合并去重（同 code 合并 models/pages）：[`by-code/`](./by-code/)（[`codes.json`](./by-code/codes.json) · [`index.md`](./by-code/index.md)）。**不**并入 bootstrap。

## 说明

- PDF 文件名里的 “Owners” 并不总是车主手册：`981_/982_/991_Carrera*` 多为 **PPN 车间维修手册** 合订。
- 英意核对：驾驶手册「Check Engine」= 排放控制警示灯（MIL）；厂服技术数据 **OBDII / EOBD**；诊断插座英文 *diagnostic socket*。
- 源 PDF 仅本机对照（可不入库）；已摘录进本目录 `obd-faults.md` / `by-code/`。

## 与 App 边界

- **不写 ECU**（ADR 001）；手册里的「清除故障记忆」仅作原文归档，产品只读。
- 不把本目录条目自动合并进 `bootstrap.json`。
