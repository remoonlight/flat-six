# Porsche 981S · PCM4 + Bose MOST150 · 降噪与 CAN 诊断结论

> 整理日期：2026-07-12  
> 车辆：Porsche 981S（已改装 PCM4.0 + 原厂链路 Bose MOST150）  
> 现有设备：Launch X431 Pro3S、OBDLink MX、PIWIS + PT3G；PCM4 已可通过 MIB 获取 SSH

---

## 1. 核心结论（一句话）

| 目标 | 结论 |
|------|------|
| 舱内「更安静」 | 981/改装 PCM4 **无真 ANC**；补偿只会抬音乐，不消路噪 |
| ≥130 km/h 音量变大 | **Bose AudioPilot（噪音补偿）** 正常/偏激工作，非故障 |
| 更低时速启用、高速更大音量 | **原厂链路做不到精细曲线**；需关 AudioPilot + DSP 车速音量 |
| PIWIS/X431 功放设码 | 多为 dataset/配置/CP，**不是** AudioPilot 灵敏度表 |
| PCM4 SSH | 改不了功放内补偿曲线；通话麦灵敏度 ≠ AudioPilot |
| CAN → Cursor | 需硬件抓包 → CSV/ASC → 再进 Cursor 分析 |

---

## 2. 现象与机制

### 2.1 用户确认的现象

- 已加装原厂链路降噪麦，并启用噪音补偿  
- **仅在约 130 km/h 以上**明显提高音量，减速后恢复  

### 2.2 AudioPilot vs 真 ANC

| | 真 ANC | Bose AudioPilot（本车） |
|--|--------|-------------------------|
| 做什么 | 反相抵消舱噪，舱内应变「静」 | 麦侦测舱噪，**抬音量/改 EQ** 让音乐听得见 |
| 高速主观感受 | 背景噪变小 | **节目声突然变大、更轰** |
| 本车是否具备 | 基本无 | 有（麦 + 功放 DSP） |

机制示意：

```text
车速↑ → 风噪/路噪↑ → 功放侧麦电平↑ → AudioPilot 补偿
       → 抬总音量 / 抬被掩蔽频段 → 「声音放大」
减速 → 舱噪↓ → 补偿收回
```

约 130 km/h 才触发：持续风噪才稳定超过算法阈值（非可调「车速开关」）。

---

## 3. 麦克风与信号处理链路

### 3.1 接线（用户确认）

```text
降噪麦 ──2pin──► Bose 功放   ← AudioPilot 参考麦（决定补偿）
       ──2pin──► PCM4.0     ← 通话 / 语音麦（免提）
```

- 功放侧 2pin：补偿相关  
- PCM 侧 2pin：通话相关；**不负责**高速自动抬音量  

### 3.2 处理位置

- AudioPilot 算法运行在 **Bose 功放内部 DSP**（非 MIB 主机）  
- PCM 主要通过 MOST 做 **开/关** 类控制  
- 功放：MOST150  

### 3.3 PCM 音效相关建议（听感）

- 讨厌自动变响 → PCM：**关闭 AudioPilot / 噪音补偿**  
- 另可试：**Linear 开**、**Surround 关**（减少额外处理）  
- 「Linear」是 EQ 曲线倾向，不是车速音量滑条  

---

## 4. 设码 / SSH 能力边界

### 4.1 PIWIS / X431 · 扬声器功放（Bose MOST150）

**能做的（常见）：**

- 自动编码 / 参数化（dataset）— 按车型写配置、恢复有声  
- 组件保护（CP）相关  
- 故障码、MOST 环诊断  
- 设备识别（外置功放、布局等）  

**公开资料中未见的可调项：**

- AudioPilot sensitivity  
- 起始车速 / 阈值 km/h  
- gain vs speed（dB 曲线）  
- AudioPilot 专用麦增益表  

厂方相关操作多为「用纠正后的 data record **重新自动编码**」，不是开放补偿曲线。

X431：读码/部分编码往往可用；完整 dataset/参数化仍以 **PIWIS** 更完整。

### 4.2 PCM4 MIB SSH

| 能力 | 对「更早启用 + 高速更大音量」 |
|------|------------------------------|
| FEC / CarPlay / GEM 等 | 无关 |
| 麦克风灵敏度类适配 | 主要影响 **PCM 通话麦** |
| rootfs 搜 AudioPilot 曲线表 | 社区无公开可改映射 |
| MOST 开关 AudioPilot | 通常仅开/关 |

**结论：SSH ≠ 能改 AudioPilot 曲线。**

### 4.3 建议用诊断仪做的「盘点」（未做则建议补做）

进入 **Loudspeaker amplifier (Bose)**，导出/截图：

1. 编码 / 长编码 / 适配通道完整列表  
2. 实测值中 Microphone / AudioPilot / Noise 相关项  
3. 编程/参数化子菜单名称（先看清单再改）  
4. 开发者模式隐藏适配（若有）  

关键字检索：`AudioPilot` / `Noise` / `Kompensation` / `Mikrofon` / `Speed` / `GAL` / `GVL`

- 有数值通道 → 备份后再试调  
- 仅有自动编码、无曲线项 → 确认原厂路不通，转 DSP  

**改码前务必备份当前编码。**

---

## 5. 若要「更低时速启用、高速更大音量」

原厂 AudioPilot **无法**提供可编辑车速–增益表。可行路径：

```text
PCM4：关闭 AudioPilot
     ↓
MOST150 →（可选 Zen-V / SPDIF）→ DSP 功放
     ↑
车速输入：CAN / GPS / 仪表脉冲
     ↓
自定义曲线示例：
  0–60   :  0 dB
  80     : +2 dB
  100    : +4 dB
  130    : +6 dB
  160+   : +8～10 dB
```

注意：**不要** AudioPilot 与 DSP 动态同时开启（易重复抬音、泵气）。

不推荐：给功放麦加前置增益硬提前触发（城区乱抬、失真、通话受影响）。

真要舱内安静：隔音、密封、轮胎；与补偿无关。

---

## 6. CAN 数据 → Cursor 分析

### 6.1 角色分工

```text
Drive CAN 分接 → 硬件抓取 → CSV/ASC 日志 → Cursor 写脚本分析
PIWIS / X431 报告 → 同项目作对照参考
```

Cursor **不能**直接连车抓 CAN。

### 6.2 981 总线现实

- OBD 口：多为网关后的诊断 CAN，丰富底盘/动力数据常被挡  
- Drive CAN（约 **500 kbit/s**）：脚窝/网关线束分接才有完整流  
- 社区常见线色（981/982，需万用表核对）：棕黑 CAN-H / 棕棕 CAN-L；**接通向网关的那对，不是只通向 OBD 的那对**  
- **不要额外加 120Ω 终端**

### 6.3 工具建议

| 方案 | 硬件 | 到 Cursor |
|------|------|-----------|
| A（首选） | CANable 2.0 或 Peak PCAN-USB + SavvyCAN | 导出 CSV/ASC |
| B（复用现有） | OBDLink MX + Drive CAN 假 OBD 口 | RaceChrono / App → CSV |
| C（路测） | CSS CL1000 等 | SD → CSV |

诊断优先级：**PIWIS 主诊 → X431 辅助 → 原始 CAN 抓包**（间歇/遥测/逆向时）。

### 6.4 建议项目目录

```text
porsche-981-can/
  logs/can/          # SavvyCAN CSV / ASC
  logs/piwis/
  logs/x431/
  dbc/               # 自建或社区 PID 映射
  scripts/
  notes/
```

### 6.5 CAN 对「路噪」的价值

- 有用：排查机械/电子异常（轴承、轮速、传感器等）  
- 无用：直接「算出降噪 EQ」或改 AudioPilot  

---

## 7. 设备用途速查

| 设备 | 适合 | 不适合 |
|------|------|--------|
| PIWIS + PT3G | 全模块诊断、功放参数化/编码、引导测试 | 原始 CAN 嗅探；调 AudioPilot 曲线 |
| X431 Pro3S | 多系统读码、部分编码、对照 | 完整 Bose dataset（常弱于 PIWIS） |
| OBDLink MX | OBD PID；接假口后可 CAN 模式遥测 | 原车 OBD 上完整 Drive CAN |
| PCM4 SSH | 主机侧补丁/菜单/通话相关 | 功放内 AudioPilot 表 |
| Cursor | 分析导出的码单、CSV、报告 | 直接抓总线 |

---

## 8. 推荐行动清单

1. [ ] PCM 确认 AudioPilot 开关位置；按需求开关做高速对比  
2. [ ] PIWIS/X431 导出 Bose 功放适配/编码完整列表 → 检索有无灵敏度类通道  
3. [ ] 若无曲线通道：规划 **关 AudioPilot + DSP 车速音量**（MOST150 保留或 Zen-V）  
4. [ ] 路噪本体：隔音 / 轮胎 / 密封（与补偿分开）  
5. [ ] 若做 CAN 分析：Drive CAN 分接 + SavvyCAN/CANable，日志进 Cursor；PIWIS/X431 仅作参考  

---

## 9. 参考链接（二次来源，供继续查阅）

- [planetkris · Unlocking Porsche 718/981 Drive CAN](https://planetkris.com/unlocking-the-porsche-718-can-bus/)  
- [SavvyCAN](https://savvycan.com/)  
- [Rennlist · Bose amp functional blocks / AudioPilot 讨论](https://rennlist.com/forums/997-forum/1056474-bose-amp-inside-view-and-functional-blocks.html)  
- [Planet-9 · Bose Linear / AudioPilot 听感](https://www.planet-9.com/threads/if-you-have-a-981-with-bose-read-this.96062/)  
- NHTSA / Porsche 技术通告：Bose 功放重新自动编码（dataset）、AudioPilot 动态噪音补偿描述  

---

## 10. 待用户补充（便于下一步落地）

- [ ] PIWIS/X431 功放适配通道清单（有则可贴出逐项判定）  
- [ ] 是否已有 Zen-V / Match·Helix 等 DSP  
- [ ] 是否继续建 `porsche-981-can` 本地分析项目  

---

*本文档为对话结论整理，非保时捷官方维修手册。改码/分接 CAN 有风险，操作前备份，接线注意安全。*
