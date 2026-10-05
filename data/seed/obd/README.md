# 981 诊断参考资料

`981-reference.json` 由 `node scripts/build-obd-981-reference.mjs` 生成，供实时 OBD 使用。它不是诊断协议或实车标定文件。

使用与产品范围见 [当前 OBD 说明](../../../docs/obd-current-guide.md) 与 [需求](../../../docs/requirements.md)；[故障与数据分析设计](../../../docs/obd-fault-data-analysis-requirements.md) 保留来源端历史 UI。逐码清单：[obd-manual-data-needs.md](../../../docs/research/obd-manual-data-needs.md)。页面退役不取消完整资料核查，候选库不等于当前可执行诊断流程。

- 按用户要求统一收录 981 和 982，仍称 981 故障库，不设置车型切换。包含 **1,106** 个代码：504 个有 981 来源、813 个有 982 来源，交集 211；排除 991。`sourceVariants` 保留原始车型、条件与证据，同码合并不代表阈值或本车适用性可合并。不能据此推断适配器能读取这些代码。**不要把「仅 504 条 981 索引」写成当前产品库规模。**
- P0571 / PSM 4340 / P0420 有部分人工核查的检查要点；具体页码保留在 `inspectionEvidence`，不是完整诊断流程验收。其余 `checks` 为空时应显示缺口。产品隐藏来源文件、页码及来源说明，内部数据保留追溯，问题代码和适用条件不得被来源清理规则误删。
- 迁入记录的原件路径为 `Z:\porsche\981`，不证明本机该路径存在，PDF 不复制入项目。981 的 ArialUnicodeMS/SimSun 存在 CMap 偏移，已有候选文字恢复记录；982 使用按版面排序的文字抽取。抽取后仍有残字和跨页风险，不自动升级为已核查；重新抽取前核对本机实际原件与版本。
- `manual-source-inventory.json` 记录原件与抽取边界：**1,081** 个代码有诊断正文证据，**25** 个仍仅索引；`manual-extra-codes.pending.json` 的 **21** 个索引外标题候选尚未核实，不自动入正式库。
- `manual-data-needs.json` 保存全部 1,106 个代码的原始候选、来源分支、条件、页码和缺口；**939** 个有规范化数据需求，**167** 个暂无对应，共 **50** 类候选。**完整诊断步骤核查 = 0。** `packages/domain/src/obd-manual-needs.generated.ts` 仅包含界面所需名称和关联，不带出处。候选均标为待核实，不生成厂商请求或自动诊断阈值。
- 生成流程：`python scripts/extract-obd-manual-needs.py`（需要 PyMuPDF，本机只读 PDF，输出 `.local/obd-analysis/manual/`）；然后 `node scripts/build-obd-manual-needs.mjs` 生成内部候选库、精简运行时数据和 `docs/research/obd-manual-data-needs.md`。人工检查要点另用 `node scripts/build-obd-981-reference.mjs`。原件版本变化后，须重新核对抽取范围与字体修复规则。
- `modules` 为 X431 Porsche V24.58 选择 981 后默认首屏的 7 个单元，来自 2026-09-13 台架 UI 归档 `.local/obd-bench/2026-09-13/ui-after-back.xml`。诊断失败后列表会重新排序，不能用失败后的首项推导默认顺序。`moduleOrderComplete=false` 表示后续顺序尚缺证据。
- 标准 OBD CAN 响应地址不能未经核实直接映射为这些厂商单元。未知响应单元保留其地址身份，待实车比对后增加映射。

协议核对参考 [ELM327 官方数据手册](https://www.elmelectronics.com/wp-content/uploads/2016/07/ELM327DS.pdf)：第 14 页说明 CAF1 + H1 保留收到的 PCI，发送时仍由适配器补 PCI；第 34 页说明 CAN DTC 响应包含数量字节；第 35 页说明 Mode 04 的清除范围、44 应答以及永久故障码不由 Mode 04 删除。软件解析、清码和复读应分别测试，不能靠模拟数据声称实车成功。
