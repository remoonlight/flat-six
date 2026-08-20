# PETKA 981 EPC 明文归档

本机 PETKA GUI 合并导出（零件表 + 装载/特殊/标准设备页）。**禁止**解析 `DATA\PO` / `.zgd`。

| 文件 | 说明 |
|------|------|
| `PETKA_Porsche_981_EPC.csv` | 权威表（`record_type=part` 的 OEM + 中英文名） |
| `PETKA_Porsche_981_EPC.md` | 人读摘要（元数据 / 材料单 / 字段说明） |

工作副本同时在 `.local/petka-epc/`（gitignore）。CSV/MD 体积约 1.6MB，本目录正文不入库 Git。

元数据（导出当时）：车型 `981` / `981SP`，年款 2016 (G)，零件记录 8916，唯一零件号 5216。

入库（覆盖 `parts.oem_number` / `name_zh` / `name_en` / `petka_note` / `pr_label`，不改价）：

```bash
npm run apply:petka-epc
npm run apply:petka-epc -- --dry-run
```
