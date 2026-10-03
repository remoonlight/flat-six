# PIWIS 981 / 982 文件归档

归档日期：2026-10-01T16:03:16.607836+00:00。仅离线资料归档。

原件位置：`.local/piwis-archive/981-982/2026-10-01/files/`。原始固件容器保持完整，共享规则原件可能包含其他车型分支；其他车型专属容器未复制。本轮扫描范围为共享 9x1 平台的 flash-data 与配套维护配置，不代表所有外部更新介质。

本次保存 299 个原件，其中 269 个 PDX/ODX 容器，436.81 MiB。
包含 293 条 981/982 展示记录；共享规则可能在两个车系下各显示一次，不等于固件数量。所有原件与主机 SHA-256 相符，PDX 内部 CRC 已检查。

- [981/982 文件与版本 Markdown 索引](981-982-files.md)
- [规则修订时间线 Markdown](981-982-timeline.md)
- [文件、系统、车型、版本及时间 CSV](../../../../.local/piwis-archive/981-982/2026-10-01/files.csv)
- [规则、车型配置与文件对应表](../../../../.local/piwis-archive/981-982/2026-10-01/rules.csv)
- [规则版本时间线 CSV](../../../../.local/piwis-archive/981-982/2026-10-01/timeline.csv)
- [完整归档清单与容器元数据](../../../../.local/piwis-archive/981-982/2026-10-01/manifest.json)

## 版本与时间的含义

PIWIS 应用版本 `24.0.3_E4`；共享脚本版本 `9x1-42.80.00-P, Build_01`。二者不等于 ECU 固件版本，原始 version-info.properties 与 vp.properties 已保留。
源文件修改时间、创建时间、ODX 导出时间、ODX 文档修订历史、规则表修订日期分列记录。ODX 历史可能继承旧模板，不能当作本车型发布日期。未核实的固件发布日期留空/标为未核实，不从零件号或文件时间推算。

## 系统与文件覆盖

| 系统 | 981 规则 | 982 规则 | 文件依据 |
|---|---:|---:|---|
| Airbag | 7 | 6 | 目标软件号/数据集 session；共享适用性仍待核实 |
| DME | 87 | 107 | 目标软件号/数据集 session；共享适用性仍待核实 |
| Gateway | 2 | 6 | 目标软件号/数据集 session；共享适用性仍待核实 |
| PDK | 6 | 33 | 目标软件号/数据集 session；共享适用性仍待核实 |
| 功放 | 5 | 0 | 目标软件号/数据集 session；共享适用性仍待核实 |
| 右LED前灯 | 6 | 7 | 目标软件号/数据集 session；共享适用性仍待核实 |
| 右前灯 | 2 | 2 | 目标软件号/数据集 session；共享适用性仍待核实 |
| 左LED前灯 | 6 | 7 | 目标软件号/数据集 session；共享适用性仍待核实 |
| 左前灯 | 2 | 2 | 目标软件号/数据集 session；共享适用性仍待核实 |
| Gateway（历史容器） | — | — | 3 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| PASM / PADM | — | — | 1 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 前车身 | — | — | 4 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 后车身 | — | — | 6 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 电子驻车制动 | — | — | 1 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 空调控制面板 | — | — | 1 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 转向助力 | — | — | 1 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 转向柱模块 | — | — | 1 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |
| 仪表 | — | — | 已保存 3 个文件名明确标注 981 的容器；未建立精确车型/硬件规则映射 |
| PCM | — | — | 本轮未找到更新 CD/SD 介质，保留既有官方通告/菜单依据 |

文件名明确标出 981/982、但未被当前规则引用的历史容器也保留；其身份来自文件名，不表示当前车辆适用。`NO FLASH` 分支只归档限制规则，不生成固件目标。

## 规则表版本时间节点

| 来源 | 时间范围（规则表） | 最近日期的版本 | 最近描述原文 |
|---|---|---|---|
| DME-flash-rules.xml | 2012-10-22 ～ 2023-09-28 | 124 | Fehlerbehebung Basis ULEV PDK MJ |
| GETRIEBE-flash-rules.xml | 2012-10-30 ～ 2023-08-16 | 85 | Nachtrag MOPF25/23 SpyderRS Stände |
| flash-rules/LL_EnginContrModul1UDS.xml | 2019-09-03 ～ 2020-03-29 | 3 | GT3RS/Speedster hinzugefügt |
| flash-rules/AIRBAG_9x1.xml | 2012-09-18 ～ 2022-10-26 | 30 | PSL F83EA in Spyder- Zeile ergänzt (HW TNR.: 201.11) |
| flash-rules/E5K1G.xml | 2019-10-18 ～ 2019-10-18 | 1 | 未记录 |
| flash-rules/GATEWAY.xml | 2021-12-16 ～ 2021-12-16 | 1 | Erstbefüllung 9x1/982 GW |
| flash-rules/VERSTAERKER.xml | 2011-07-18 ～ 2016-05-17 | 5 | Boxster Spyder eingefügt |
| flash-rules/SCHEINWERFER_LINKS.xml | 2016-02-03 ～ 2016-02-03 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_RECHTS.xml | 2016-02-03 ～ 2016-02-03 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_SW.xml | 2015-03-20 ～ 2015-03-20 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_LED_LINKS_9X1_DS.xml | 2016-03-21 ～ 2024-03-25 | 3 | Fahrzeugschlüssel F83TA zu P203 hinzugefügt |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_SW.xml | 2015-03-20 ～ 2015-03-20 | 1 | 未记录 |
| flash-rules/SCHEINWERFER_LED_RECHTS_9X1_DS.xml | 2016-03-21 ～ 2024-03-25 | 3 | Produktschlüssel F83TA zu P203 hinzugefügt |

## 其他车型

[其他车型 Markdown 索引](other-models.md) 仅保存文件名、系统线索、源时间和原始规则描述，不保存其专属 PDX/ODX。共享容器若被 981/982 规则引用，或属于本轮明确列出的共享系统候选，保留共同原件并标明待核实依据，不据此宣称车型适用。

## 复核

```powershell
python scripts/archive_piwis_workshop.py --verify
```

本目录 Markdown 为项目索引；`.local/piwis-archive/` 内原件、清单与 CSV 仅保存在当前项目电脑，不随 Git 或安装包发布。归档不授予刷写资格。
