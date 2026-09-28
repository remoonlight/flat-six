# 捆绑目录快照（clone 可用）

公开价目录，从本机缓存**拷贝价/目录字段**入库。不含车库数据库、VIN、客户或服务记录。

| 文件 | 内容 | 来源 | as-of |
|------|------|------|--------|
| `981.csv` / `982.csv` | 世代公开 OEM 价表 | `.local/teile-bulk/{981,982}/parts.csv` | 行内 `price_as_of`（约 2026-08-02） |
| `teile-oem-exact.json` | 精确 OEM EUR 价（compact === priceNumber，有限正价） | `.local/teile-oem/hits.json`（`scripts/fetch-teile-oem.mjs` 核 pn） | 条目 `as_of`（约 2026-08-18） |

- 币种：CSV 行内 `currency`；精确命中为 **EUR**，`source=teile.com`，`petka_verified=0`
- 精确命中**只填已有目录行**（981/EPC 或保养 sku 且 OEM 一致），不凭价新建兼容件
- 不含 Design911 模糊匹配或 AI 译名
- 981 全量条目缺 OEM 价时，可复用相同 OEM 的保养条目报价，保留来源、日期和 `quote_sku`；不复制副厂价。
- 启动合并不覆盖用户改过的价格/币种备注/自定义行；同源 hash 再次启动不回填已清空的价、不复活已删行
