# 实时 OBD — 需求 Plan（产品方向）

> 状态：2026-08-02 **Phase 1 已交付**（码库查码 + 手工会话，无硬件）；Phase 2+ 见下表。  
> 一级菜单：`ObdPage`（查码 + 故障台账过渡；**设码子页并入本 Tab**）。  
> 相关：ADR `001-no-ecu-write.md`（仍禁止写 ECU）；旧需求「不做 App 内 OBD」**废止，以本文为准**。

---

## 1. 目标（人话）

插上适配器后：

1. 能读并**落盘**当前故障码（含历史/待处理会话）。
2. 能采一批**实时运行数据**（转速、水温、负荷等）做成会话记录。
3. App **内置**常见故障码的说明与处理建议（先扩现有知识种子）。
4. **以后**把会话交给 AI 出方案（本 plan 只定数据接口，不做 AI）。

不做：写 ECU、刷隐藏功能、替代 PIWIS/X431 设码写车。

---

## 2. 分期

| Phase | 交付 | 验收口径 |
|-------|------|----------|
| **0** | 一级空壳 + 本文 plan | 菜单可进；文案指向本文 |
| **1** | 码库 UI + 手工/粘贴码 → 查内置解决办法；会话表结构 | 不插枪也能查码；种子 ≥ 约定条数 |
| **2** | 选适配器 + 读码（DTC）落盘；**不做清码** | 本机实测 981 读到码或「无码」 |
| **3** | 实时 PID 采样落盘 + 简易仪表 | 固定 PID 集可录一段会话 |
| **4** | AI 方案入口（可选本地/外呼） | 输入会话 ID → 返回建议；可关 |

当前工程：**Phase 1 完成**（`accept:obd-phase1`）；硬件读码待 Phase 2。

---

## 3. Phase 1 数据（先于硬件）

### 3.1 内置码库

- 来源：**扩现有故障知识种子**（拍板 Q6=A）。
- 字段建议：`code`（P0xxx / 厂商码）、`title_zh`、`likely_causes[]`、`checks[]`、`locate_zone?`、`severity_skus?`。
- UI：搜索码 → 展示解决办法；可「记入会话」不连车。

### 3.2 会话落盘（SQLite 草案）

```text
obd_sessions(id, started_at, odometer_km?, adapter?, note)
obd_dtcs(session_id, code, status, raw_json?)
obd_samples(session_id, t_ms, pid, value, unit)
```

与现有 `fault_logs`：**短期并存**；Phase 1.1 已支持会话展开看码 +「记入故障台账」（复用 `addFaultLog`，码库字段映射 symptom/area/action）。

**种子增量**：`seedDtcMissing` 在已有 `dtc_kb` 时只插入 bootstrap 中缺失的 code（不覆盖已有行）。

---

## 4. Phase 2+ 硬件（待选型，本轮不定死）

候选（实现前再收敛一次）：

| 路线 | 优点 | 风险 |
|------|------|------|
| ELM327 USB/BT + 标准 OBD-II | 便宜、资料多 | 981 厂商码/多 ECU 覆盖差 |
| 开源栈（如 `python-OBD` / 自研 AT） | 可控 | Windows 驱动/权限 |
| 商业 API 适配器 SDK | 稳 | 成本、闭源、本地-only 冲突 |

约束：

- **仅 Windows 本机**（ADR 002）。
- **只读**；清码若做必须二次确认且默认关。
- 不上传云。

---

## 5. 与 flat-six Live OBD 的关系

参考 [flat-six.org/obd](https://www.flat-six.org/obd)（BETA）的产品形态（实时+诊断），**不**依赖其账号/云。本 App 自建本地会话与码库。

---

## 6. 明确不做（本 plan）

- 写 ECU / 编码 / 刷写 / **清码**（仍走本 Tab 内「设码」剧本 + X431 人工作业；ADR 001）
- 首页未关闭工单看板
- Phase 1 不强制真车联调；Phase 2 起需适配器实测

---

## 7. 下一步（Phase 2）

1. 适配器选型与本机 981 实车读码验证（DTC 落盘，仍不清码）。  
2. 码库继续扩覆盖（PDK / 车身舒适等；当前种子 49 条）。
