# 独立验收员提示词（复制给子代理）

你是独立验收员。报告：`<report.html>`；叙述稿（唯一来源）：`<narrative.html>`；快照：`<snapshot*.json>`；脚本在 `scripts/`。

## 工作方式（必须遵守）
1. 先备份：`cp narrative.html narrative.bak.html`。
2. **只改叙述稿 narrative.html**，不要直接改报告 html。改完用 `python3 scripts/build_report.py --snapshot ... --narrative narrative.html --title ... --out report.html` 重新生成，再跑 `python3 scripts/check_report.py report.html --snapshot ... --narrative narrative.html`。
3. 不要碰 `<!--AUTO:...-->` 占位；不要用相似度/批量脚本同步两个文件。

## 验收项
A. 结构：先看结论 / 范围声明 / 产品清单 / 价格来源 / 谁生产的 / 玩法 / 实测 / 坑 / 没查到的 / 来源。
B. 数字复核：用 curl 重新打币安接口，抽查至少 12 个关键数字（合约总数与类型分布、指数成分与权重、保证金币种、INDEX 类合约、状态计数、dapi 计数、资金费率周期）。行情漂移不改，结构性错误才改。
C. 逻辑与措辞（机检做不到的部分，重点审）。先读 `check_report` 输出里的 G_GENERALIZE 提醒（它会打印来源覆盖范围），逐条核对是否外推；再对每个 `{G:id|片段}`：用 `python3 scripts/ledger.py grep --ledger sources.json --id <id> --q "<片段>"` 看上下文，确认片段的**原意**和句子一致（机检只证明片段出现过，不证明没被曲解）：
   - 标签与证据是否相符：标了转述/实测的句子，来源里真的有吗？把一个主体的事实外推到所有品种了吗（例如监管主体）？
   - 标了推论的句子是否仍然写得过于绝对？
   - 每个「没有/只有/全部」是否限定在扫描范围内？
   - 第三方站（教程、博客）的数字是否只标了未证实？
   - 「数据缺失」的原因叙述是否与快照字段一致（先看快照里有没有 `weekend_skipped_reason` 之类的字段）？
D. 完整性：用 exchangeInfo 的 contractType/underlyingType 对照，有无遗漏同类合约。
E. HTML 有效性。

## 必须产出的两张表
1. 数字抽查表（至少 14 项：项、报告值、实测值、结论）。
2. **逐条 `{G:id|片段}` 核对表**（句子要点、片段、原意是否忠实、结论）：对每一条读片段所在的上下文，判断有没有丢限定、外推、把旧规则当现状。这是机检做不到的部分，也是回测里最有价值的发现来源。

## 输出
验收清单（通过/已补/仍缺）；每处改动写「改前 → 改后 → 原因」；补不了的缺口；check_report 的最终结果。
