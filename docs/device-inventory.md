# 项目诊断与采集设备清单

更新日期：2026-10-01。当前状态快照：22:31，Asia/Shanghai。

本清单记录本项目在这台 Windows 电脑上安装、识别或实际使用过的诊断与采集设备，也列出相关驱动来源主机。当前可以发现 PT3G/E70 USB 模块，以及已配对的 vLinker 和 OBDLink MX+；VNCI 与 X431 平板有历史识别证据，本次快照未见其 USB 在线。

“已配对”“驱动可用”“已读电压”“已读车辆数据”分别记录，不能互相替代。项目接入状态指本机工作区；本次文档提交不包含正在开发的代码、厂商驱动文件或原始车辆日志。

## 已安装或识别过的设备

| 设备 | 已核对身份 | 电脑连接方式 | 当前状态 | 已有实际验证 |
| --- | --- | --- | --- | --- |
| PT3G，实际使用 E70/Eucleia 通信层 | 原生 API 报告 `PT3G-VCI`、序列号 `5779657`；USB `VID_0BDA / PID_8152` | Realtek USB FE 网卡 + 本地 E70 服务 | USB 网卡在线；原生模块 `available`；服务 Running | 管理页面与固件版本读取、D-PDU 构造/枚举/释放成功；未进行车辆通信 |
| VNCI VAS6154A | 原生序列号 `6055836`；USB `VID_103A / PID_6154`；硬件 `IR14690` | USB RNDIS + VW D-PDU API | 驱动已安装；本次枚举为空；此前已识别 | 固件校验、原生电压 `11.635 / 11.642 V`、正常关闭；三个协议离线通道配置成功；未读实车 ECU |
| vLinker FS BT | 蓝牙名称 `vLinker FS 11436`；MAC `04:25:E8:5B:D4:CB`；项目 ID `bt:0425E85BD4CB` | Windows 蓝牙 SPP，当前映射 `COM9` | Windows 已配对，唯一 SPP 端口可枚举；本次未打开串口 | 身份握手、供电读取、具名 981 DME/Gateway 身份与故障码、标准六参数熄火/怠速读取成功 |
| OBDLink MX+ | 蓝牙名称 `OBDLink MX+ 79565`；MAC `00:04:3E:5A:A2:79`；项目 ID `bt:00043E5AA279` | Windows 蓝牙 SPP，当前映射 `COM10` | Windows 已配对，唯一 SPP 端口可枚举；本次未打开串口 | 实际型号/固件握手、`14.1 V` 供电读取、Drive CAN 广播接收；接收有质量错误，见下文 |
| Launch X431 Pro3S 诊断头 | 从平板 HCI 原始记录识别到蓝牙地址 `00:18:E4:44:A9:D4` | 诊断头与 X431 平板蓝牙通信；电脑经平板 ADB 获取原始日志 | 有历史实测记录；本次未验证诊断头在线 | 平板诊断头连接与电压显示、数据流批量留存、部分设码原值收存；不是本项目独立设码验证 |
| X431 使用的 Android 平板 | ADB 实测型号 `Lenovo TB2-X30M`；应用 `com.cnlaunch.x431.pro3S` | USB ADB | 曾通过 ADB 识别并采集；本次 USB 快照未见该平板 | 型号查询、HCI 文件读取、连续增量归档与退出后的文件一致性核对 |
| Espressif USB 调试/串口设备，完整型号未确认 | 历史 USB `VID_303A / PID_1001`；描述 `USB JTAG/serial debug unit`；曾映射 `COM7` | USB 调试/串口 | 历史枚举记录；本次未发现 | 仅确认过 USB/串口呈现；不能据此认定具体 ESP32 型号、CAN 收发器或可用 CAN 接口 |

COM 号是本次或历史系统映射，不是设备固定身份。蓝牙设备连接时仍须按 MAC 重新匹配，不能直接扫描其他串口。

## 驱动与接口安装记录

### PT3G E70

最初安装了 PIWIS 主机上的 ACTIA 官方 PT3G 驱动，随后查明本机这只诊断头还需要 E70 通信层。两部分均已安装，实际设备发现由 E70 完成。

| 项目 | 安装或识别结果 |
| --- | --- |
| ACTIA 官方安装包 | `PT3G-VCI Driver Package 1.0.9.59`；ACTIA 数字签名有效；安装器、USB/底座安装步骤均 exit 0；无需重启 |
| ACTIA 安装路径 | `C:\Program Files (x86)\ACTIA I+ME GmbH\PT3G-VCI Driver\` |
| Windows 驱动库 | `oem217.inf`：`pt3gvci-usb-driver.inf`；`oem218.inf`：`pt3gvcidock.inf` |
| ACTIA 定位库 | 原生与 WOW6432Node 分别注册 `PT3GVCILocate64.dll`、`PT3GVCILocate.dll`；64 位库加载通过 |
| 本只设备的 USB 网卡驱动 | Realtek `11.19.602.2025`，`oem162.inf`；不是上述 ACTIA `VID_103A / PID_0014` 的 RNDIS 设备 |
| E70 驱动路径 | `C:\ProgramData\PORSCHE-VCI\`；服务 EXE 与 `X64\PDU_VCI.dll` 等 8 个源文件来自 PIWIS 主机 |
| E70 Windows 服务 | `VciToolServerPORSCHE`；进程 `E70_PT3G_SERVICE.exe`；Auto / LocalSystem；当前 Running |
| E70 原生接口 | `C:\ProgramData\PORSCHE-VCI\X64\PDU_VCI.dll`；配套 `pdu2.dll` 文件版本 `24.1.0`，其版本资源标为 VW D-PDU API |
| 本机协议检测设置 | `X64\PDU_VCI.ini` 中 `15765StartDetect=0`；关闭自动 CAN 协议检测；源配置原件保留在本机归档 |
| USB 网卡与管理地址 | 「以太网 7」当前 `169.254.31.45/16`；设备管理地址 `169.254.100.1:80`，固定该网卡 GET 成功 |
| 管理页面版本 | `E70 V2.00.03`；仅查询页面，没有上传固件或更改管理设置 |
| 原生模块报告 | 类型 `11`，handle `1`，USB，序列号 `5779657`，状态 `0x8063`；报告的 `127.0.0.1` 是通信层本地代理地址 |
| 已验证调用 | `PDUConstruct`、`PDUGetModuleIds`、`PDUDestroyItem`、`PDUDestruct` 全部返回 0 |

序列号 `5779657` 来自原生驱动，未核对实物铭牌。源 `PDU_VCI.ini` 中还存在 `SN=5154824` 配置值，不能拿该配置值替代实际枚举报告。E70 文件没有独立数字签名，已经核对关键文件与源主机的 SHA-256 一致；ACTIA 安装器的签名结论不适用于这些 E70 文件。

本机工作区尚未把 PT3G/E70 加入桌面诊断传输入口。驱动安装与模块枚举成功不等于车辆读取、CAN 收发或设码能力已验收；本轮没有调用该设备的 `PDUModuleConnect`、`PDUConnect` 或 `PDUStartComPrimitive`。

### VNCI VAS6154A

| 项目 | 安装或识别结果 |
| --- | --- |
| 定位驱动 | ACTIA/IME VAS6154 Driver `2.0.12.100`；注册 `C:\Program Files (x86)\Volkswagen\VAS6154 Driver\VAS6154Locate_64.dll` |
| D-PDU 库 | 本机工作区 `.local/vnci-support/vendor/VW_PDUAPI_OS/`；实际 `PDUAPI_VW.dll` 版本 `29.0.0` |
| Windows USB 驱动 | Microsoft RNDIS，`wceisvista.inf`，版本 `10.0.22621.1` |
| 设备固件 | `29.0r00-14769-2964`；设备 D-PDU 版本 `29.0.0` |
| 管理地址 | USB 管理端点 `192.168.13.69`；此前电脑端 `192.168.13.240/24` |
| 安装验收 | 安装日志完成、无需重启；外层进程 exit 1638，未将该退出码当作干净成功；已用实际注册、模块连接、电压读取和关闭核验 |
| 项目接入 | 本机已实现按 USB 序列号选择、原生电压监视、具名 DME/Gateway 只读传输；限定 DLL 哈希和固件版本 |
| 当前限制 | 未完成 VNCI 实车 ECU 读取；清码/设码未开放；本次快照未枚举到 USB 设备 |

初次仅接 USB 的实测值为 0 V，主进程正确拒绝连接并清除显示电压。用户随后接 12 V 外部电源，两次真实供电样本为 `11.635 / 11.642 V`，均保留时间与正常关闭记录。这些是适配器端测量，不是 ECU 上报值，也不代表此刻设备仍在线。

### vLinker FS BT

历史实际 `ATI` 输出为 `ELM327 v2.3`，115200 波特率；2026-09-26 完成配对、SPP、供电和关闭后重新打开核验。本机依赖 Windows 蓝牙 SPP 与 Python/pyserial，未把它当作 USB/J2534 设备。

2026-10-01 项目独立完成具名 DME/Gateway 身份与 DTC 只读；随后独立标准路径 `7DF → 7E8` 完成六项 PID 的 5 轮 / 30 样本，并分别留有熄火和静态怠速证据。供电样本分别出现 `12.1 V` 与 `14.1 V`。这些结果仅覆盖对应现场和请求集，不能推广为全车诊断或隐藏功能已验证。

### OBDLink MX+

实际握手输出 `OBDLink MX+ r3.1`、`STN2255 v5.9.4`，供电 `14.1 V`。已完成 Windows 配对与唯一 `COM10` 的实测打开；蓝牙配对本身不保证每次能连接，历史也出现 Windows 121 超时。

用户说明 MX+ 接车内 **Drive CAN 分接口**。先取得 608 帧短片段，因 `CAN ERROR` 与 `BUFFER FULL` 提前停止；随后固定候选 ID 筛选录制完成 20 秒、8,960 帧，仍有 124 次 `CAN ERROR`，严格质量判断 `ok=false` / exit 1。原始数据及帧顺序核对通过，但不作为无丢帧、信号公式或物理总线映射已验证的证据。

### X431 平板与诊断头

历史实测应用包 `com.cnlaunch.x431.pro3S`，版本 `7.03.023`；ADB 实测平板型号 `Lenovo TB2-X30M`。2026-09-26 已验证平板与诊断头连接、HCI 日志读取和头端页面电压 `12.02 V`。

2026-10-01 通过 USB ADB 保存既有 HCI 日志，诊断头蓝牙地址为 `00:18:E4:44:A9:D4`；留存批量发动机数据流以及部分设码原值页面/备份。HCI、ACL、RFCOMM 是平板与诊断头的通信载荷，不能标为物理 CAN 帧；页面原值和备份也不等于已证明可恢复。没有把 X431 的安全访问或设码操作自动移植为项目可执行请求。

## 驱动来源和相关主机

| 主机 | 身份 | 已确认访问方式 | 本轮作用 |
| --- | --- | --- | --- |
| 本机 Windows | `DESKTOP-4F43BNN`；当前项目工作区所在电脑 | 本机 PowerShell；WLAN 此前检查为 `10.10.10.192/24` | 运行 Electron、SQLite、蓝牙/USB 驱动、E70 服务与采集脚本 |
| VNCI 驱动来源主机 | `DESKTOP-1TOEIUD`，`10.10.10.205` | SSH 22，账号 `ric`；现有密钥成功登录 | 提供 VAS6154 定位驱动和 VW D-PDU 29.0.0；用户说明该主机防火墙仅放行 `10.10.10.0/24` |
| PIWIS 3/4 主机 | `XIAOFENG`，`10.10.10.137` | SSH 22，账号 `PorscheUser`；现有密钥成功登录 | 提供 ACTIA PT3G 包及实际 E70/PORSCHE-VCI 驱动；PIWIS 3/4 是用户说明，版本未由本轮独立验收 |

账号与主机信息用于定位既有设备；清单不包含密码、私钥、许可证或认证令牌。驱动取得后，本机安装不依赖来源主机持续在线。

## 接线附件及仅有资料的设备

| 设备或附件 | 已有证据 | 尚未确认 |
| --- | --- | --- |
| 12 V 外部电源 | 用户说明供电；VNCI 有约 11.64 V 原生测量 | 电源型号、额定电流、负载稳定性未记录 |
| CAN 一分二线 | 用户说明用来连接 PT3G 与 VNCI | 未独立测量线序、终端电阻、物理总线或是否连接车辆；不把“串接”说法当作已核实拓扑 |
| comma4 / Panda | 用户说明拥有设备；项目有采集研究/导入资料 | 缺少本项目本机实际连接、服务/固件与静默状态资格验证；SSH 配置存在不算已连接 |
| ESP32-C3 / 其他 CAN 采集器 | 项目有研究资料和历史参考；曾见 Espressif USB 调试接口 | 未建立上述 USB 接口与具体 ESP32-C3 板、CAN 收发器、固件及实测采集的对应证据 |

本节没有把“用户拥有”或导入资料写成“本机已安装并验证”。其他电脑内置摄像头、指纹、WLAN、5G 与蓝牙控制器不作为项目诊断头单独计数。

## 本机证据索引

以下材料只保存在本机，路径从项目根开始；Git 云端只提交本清单，不包含这些原始记录或厂商文件。

| 对象 | 本地证据路径 |
| --- | --- |
| 本次蓝牙/VNCI 枚举 | `.local/pt3g-support/device-document-current.json`；只读元数据，`openedPort=false` |
| PT3G 安装和识别 | `.local/pt3g-support/device-inventory.json`、`e70-install-result.json`、`e70-module-discovery-final.json`、`final-verification.json` |
| PT3G 管理页面 | `.local/pt3g-support/management-169.254.100.1-80.bin` |
| ACTIA 官方安装日志 | `.local/pt3g-support/driver-install.log`、`driver-store-after.txt` |
| VNCI 驱动/身份/无供电处理 | `.local/vnci-support/device-inventory.json`、`vas-driver-install.log`、`host-check.json` |
| VNCI 外接电源实测 | `.local/pt3g-support/vnci-powered-adapter-check.jsonl` |
| vLinker 初期接口核验 | `.local/device-preflight/2026-09-26/current-check/result.json` |
| vLinker 身份/DTC 实测 | `.local/vehicle-runs/981-20261001T083428Z/现场结果.md` |
| vLinker 标准参数实测 | `.local/vehicle-runs/981-standard-retry-20261001T090641Z/现场结果.md`；`.local/vehicle-runs/981-idle-20261001T091559Z/现场结果.md` |
| MX+ 配对与广播实测 | `.local/mxplus-support/scratch/winrt-pair-result.json`；`.local/mxplus-drive-can/20261001-175855-363788/` |
| X431 平板型号 | `.local/vehicle-runs/981-x431-coding-20261001T095850Z/device-model.txt` |
| X431 通信留存/原值采集 | `.local/vehicle-runs/981-x431-bulk-20261001T092731Z/现场结果.md`；`.local/vehicle-runs/981-x431-coding-20261001T095850Z/现场结果.md` |

## 核验与更新

设备档案应保留日期、原生身份、Windows 映射、驱动/固件版本、实际检查结果与原始证据位置。设备失联不会删除已成立的历史实测，也不能用历史样本充当当前在线状态。

当前盘点只枚举元数据与已有设备记录，没有开启蓝牙串口、车辆采集、清码、设码或固件更新。后续车辆操作继续遵守项目 ADR 001：限定已定义请求与身份匹配；独立设码逐功能建立原值备份、写入/回读和恢复方案。两只头接在同一 CAN 支路不代表可以同时主动诊断。
