# 手册 OBD / 故障相关归档（中文）

本目录保留手册摘录与历史译文，不并入 App `dtc_kb`（`../bootstrap.json`）。历史 `by-code/codes.json` 中的译文不作为工厂中文错误标题。

`manual-titles.v1.json` 是独立的原文标题参考索引，保留车型、单元、原始故障编号、语言、物理页码及源 PDF SHA-256。当前共1,359条：981 DME中文484条，982合集中的DME英文598条、PDK英文164条，以及14个其他单元的六位厂商原码英文参考113条。条数包含同码冲突，不等于唯一故障数或适配数量。新增原码分为110个“单元＋代码”组合，其中左前灯三组标题冲突，不自动选用；其余107组还须满足本次读取及来源字典核对条件。

页面只对已知车型、单元和码制匹配，不跨车型借用标题。六位原码参考明确标记 `codeNamespace: manufacturer-hex`，只在本次已确认的来源版本、车型和单元中，原始完整码与字典及手册逐字相同时使用；不截短、补字节或转换成SAE显示码。左灯章节不用于右灯，其他单元的同码也不互借。当前读取和受限清码范围统一见[OBD当前结果](../../../../OBD_STATUS.md)，标题索引不扩大车辆执行或版本资格。982合集的共享9x1章节不能仅凭文件名证明具体车型或变体适用；页首单元与页脚章节必须一致，其他车型章节、通用CAN页和单元未明确的标题留在本地研究资料。有前置适用条件的条目保留原条件，未确认变体时不自动选用。

生成器为 `scripts/build-obd-manual-titles.py`，在安装了 `pdfplumber` 的开发环境提供两个原件路径：`--981 <981手册.pdf> --982 <982手册.pdf>`。原件必须匹配索引中的 SHA-256；中文 CID 字体恢复不完整、未找到明确标题边界或无法确定单元的页不进入分发索引；逗号与括号采用原始页面已核对的嵌入字体映射，不用译文补写。拒绝项和原始解析留在本机 `--evidence-dir`，不分发私人路径与水印。多码共用标题保留每码对应的细项，原件不修改。模块从页首标题区识别，并与页脚章节核对；正文提及其他控制单元不改变归属。英文标题在注意事项或诊断正文前结束，警告图标提取成的 `8 WARNING`、`MN CAUTION`、`8 DANGER` 等仍是安全告知，不是车型条件；告知前已经出现的真正适用条件继续保留。

## 车型目录

| 目录 | 主要来源 PDF（`data/`） | 交付 |
|------|-------------------------|------|
| [`981/`](./981/) | `981_Boxster BoxsterS BoxsterGTS.pdf`（车间维修手册，中英标题）；`981_Boxster, Boxster S, Boxster GTS 驾驶手册 (0814).pdf` | [`obd-faults.md`](./981/obd-faults.md) |
| [`982/`](./982/) | `982_Boxster BoxsterS BoxsterGTS.pdf`（合集末附车主手册中文段）；`982_GT4 Owners Manual.pdf`（英，仅核对） | [`obd-faults.md`](./982/obd-faults.md) |
| [`991/`](./991/) | `991_Carrera …pdf` / `991_CarreraCabriolet …pdf`（车间维修手册）；`991_Factory Service Manual.pdf`（英厂服，核对用） | [`obd-faults.md`](./991/obd-faults.md) |

跳过：`982_Cayman Navigation Retrofit.pdf`（导航改装，无 OBD 故障专章）。

## 按 code 汇总

分车型抽取后合并去重（同 code 合并 models/pages）：[`by-code/codes.json`](./by-code/codes.json)。只维护数据文件，不另生成重复的逐码 Markdown 索引；**不**并入 bootstrap。

## 说明

- PDF 文件名里的 “Owners” 并不总是车主手册：`981_/982_/991_Carrera*` 多为 **PPN 车间维修手册** 合订。
- 英意核对：驾驶手册「Check Engine」= 排放控制警示灯（MIL）；厂服技术数据 **OBDII / EOBD**；诊断插座英文 *diagnostic socket*。
- 源 PDF 仅本机对照（可不入库）；已摘录进本目录 `obd-faults.md` / `by-code/`。

## 与 App 边界

- 手册里的「清除故障记忆」仅作原文归档，不授予车辆执行资格；当前具名受限清码及其他写入边界见 [ADR 001](../../../../docs/adr/001-no-ecu-write.md)。
- 不把本目录条目自动合并进 `bootstrap.json`。
