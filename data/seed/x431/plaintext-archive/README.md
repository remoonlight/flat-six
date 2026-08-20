# 981 / ID.3 诊断 CAN 明文归档 — 进度

目标：尽量拿到 Porsche 981 与大众 ID.3 的**诊断 CAN 相关明文**（ECU 列表、会话/服务、DID/编码通道、UDS 请求等）。

## GateGuard 创建前事实

1. **调用方**：无人读/脚本硬编码依赖；本 README 为人读进度板，路径由对话引用。  
2. **同用途检索**：`plaintext-archive/` 下原先无 README；桌面已有 `X431-981-982-ID3-设码逻辑.md`（设码逻辑综述），本文件专记「明文归档阶梯与现状」。  
3. **数据**：本 md 不读写库；旁路 CSV 字段示例 `VID,Make,Model,VIN,Year`（历史诊断）。  
4. **用户原话**：`从最简单的方法开始试,目标为获取porshce 981/大众ID.3的所有诊断can信息明文归档`

---

## 方法阶梯（由易到难）

| # | 方法 | 结果 |
|---|------|------|
| **1** | 收集本就明文的文件 | **已完成** → `981/` `ID3/` `00-already-plain/` |
| **2** | 设备报告/缓存/prodb | **基本空**；设备导出过程目录已清理 |  
| **2b** | 保时捷模块已开 → qcar「可测车型」WebView CDP | **已完成**：Boxster(981)/Cayman/982 可测功能表 → `03-sim-981/` |  
| **3** | 常见口令/MD5/SO 内 hex 当 AES 密钥 | **失败**；过程产物已清理 |  
| **4** | 本机 ODIS PDX（ID.3 最省事） | **当前未安装**（`C:\Program Files\OE|OS` 不存在） |
| **5** | 实车诊断时 Frida/hook `AES_Decrypt` dump | **下一步推荐**（仍需 VCI 进会话） |
| **6** | Ghidra 逆向 `lib*_FILE.so` 抠密钥派生 | 再下一步 |

---

## 已归档明文（可用）

### 通用

- `00-already-plain/vin_VIN_INFO.csv` — 含多台 981 + ID.3（VIN 已换成演示号）
- `00-already-plain/history_Vehicle_Porsche_VW.csv`
- `00-already-plain/history_System_Porsche_VW.csv` — 历史扫到过的系统名（近似 ECU 清单）
- `00-already-plain/history_DTC_Porsche_VW.csv`
- `00-already-plain/summary_scan.txt` / `summary_data.json` — 库符号级诊断逻辑骨架

### 981

- `981/HINI_CN.HTML` — V24.55 能力说明  
- `981/SPECFUNC.INI` `FUNC.INI` `lib.cfg` `INI_CN`  
- `981/so_symbols_diag.txt` — Coding/SPECF/FILE 相关符号  
- `03-sim-981/981_testable_functions.json` — **Boxster(981) 可测功能明文目录**  
- `03-sim-981/981_Boxster981_rows.json` / `981_Cayman_rows.json` / `982_Boxster982_rows.json` — 全量明细  
- `03-sim-981/981_SIM_ARCHIVE.json` / `981_from_history.json` / `981_history_utf8.json` — 历史诊断会话重建（VIN/ECU/DTC）  

### ID.3

- `ID3/HINI_CN.HTML` — V29.10（明确含 ID.3 + SFD + 在线编码）  
- `ID3/ADAPTMENU_CONFIG_channels.txt` — 明文通道号列表（自适应菜单，非完整 DID 语义）  
- `ID3/so_symbols_diag.txt` — LongCoding/SFD/Online 等符号  

---

## 关键结论

1. **「所有诊断 CAN 明文」无法仅靠拷贝 X431 BIN 一次得到**——主体在 YZJM/AES 内。  
2. 简单口令爆破对 `DSN` / `LONGCODEDATA` / `9X1_ALLDATA` **无效**。  
3. 当前最有价值的明文是：**帮助文档 + 自适应通道号 + 历史系统/DTC + so 符号表**。  
4. 要完整 UDS/DID/编码表，优先：  
   - **ID.3**：恢复 ODIS，导出 PDX（通常比解 X431 快）  
   - **继续 X431**：诊断时 hook `AES_Decrypt`/`Decrypt`/`ggp_Open` dump 缓冲  

---

## 下一动作（请选）

- **A**：X431 实车诊断并保存报告（PDF/云）后我再拉取  
- **B**：Frida hook 运行时解密（需平板可调试/root 或 gadget）  
- **C**：你给出 ODIS 安装路径后，直接归档 ID.3 PDX 明文  
