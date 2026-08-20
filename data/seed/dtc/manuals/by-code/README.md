# 手册故障码 · 按 code 汇总

并行分车型抽取后合并。**不**写入 ootstrap.json / dtc_kb。

## 产物

| 文件 | 说明 |
|------|------|
| [codes.json](./codes.json) | 去重后的码表（同 code 合并 models/pages） |
| [index.md](./index.md) | 按 code 排序的可读索引 |

## 统计

- **唯一码数：** 1112
- **覆盖（码出现于该车型）：** 981=504 · 982=813 · 991=21
- **跨车型同码：** 222
- **verify（合并后取最优）：** zh-only-oem=16 · zh-only=11 · matched-en=7 · translated-from-en=925 · unverified=153

## 边界

- 只读对照手册；产品查码仍走 bootstrap。
- 不写 ECU（ADR 001）。
