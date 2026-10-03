# exchange-asset-report

用币安公开接口取数（官方公告需人工/模型另查，脚本不抓公告），生成「价格从哪来 / 谁生产的 / 怎么玩 / 实测」的白话 HTML 调研报告。当前版本只覆盖**币安**（美股/ETF 永续、bStocks、大宗、外汇、指数类产品）。

核心思路：**数字交给脚本，判断交给人，验收交给另一个人。** v0.2 起，叙述里的数字也由脚本内联取数，验收按句检查标签、范围声明和措辞。

v0.2 的回归记录见 [`docs/regression-2026-10-03.md`](docs/regression-2026-10-03.md)：数字层的结构性错误从 3/14 降到 0/14，但「网页转述与解释」层仍被独立验收员挑出 28 处问题，这一层目前只能靠人工/独立验收，**机检通过不等于内容正确**。

| 层 | 做什么 | 确定性 |
|---|---|---|
| `scripts/binance_data.py` | 取数：合约清单、成交、深度、指数成分、资金费率、K 线、周末/盘后统计、现货对照 | 确定，可重复 |
| `scripts/build_report.py` | 快照 + 叙述模板 -> HTML，表格自动填，缺字段显示「数据缺失」 | 确定 |
| `scripts/check_report.py` + `claims.py` | 验收：HTML 结构、范围声明、**断言级规则**（标签、全称否定缺范围、实测句手敲数字/推测措辞/全称无证据、转述区禁实测）、快照与叙述稿哈希、`--live` 抽查 | 确定 |
| `SKILL.md` + `references/` | 方法、规则、坑、独立验收员提示词（Claude Code skill） | 需要人/模型判断 |

## 安装为 Claude Code skill

```bash
git clone https://github.com/kary2999/exchange-asset-report.git
ln -s "$(pwd)/exchange-asset-report" ~/.claude/skills/exchange-asset-report
```

## 单独使用脚本（只需 Python 3.9+，无第三方依赖）

```bash
# 1 取数（survey 做全品类盘点并记录扫描范围，其余类别取逐合约数据）
python3 scripts/binance_data.py --category survey --out snap_survey.json
python3 scripts/binance_data.py --category commodity --pairs PAXGUSDT:XAUUSDT --out snap.json
# 2 复制 references/narrative-template.html 写叙述，用 <!--AUTO:contracts--> 等占位
# 3 生成
python3 scripts/build_report.py --snapshot snap.json --narrative narrative.html --title "币安大宗商品玩法拆解" --out report.html
# 4 验收
python3 scripts/check_report.py report.html --snapshot snap.json --narrative narrative.html --live
```

类别：`survey`、`commodity`、`fx`、`index`（读 `config/index_watchlist.json`）、`equity`、`custom`（配 `--symbols`）。

## 它能保证什么、不能保证什么

能保证：自动表格里的数字来自快照；缺字段显示「数据缺失」；接口返回的字符串已转义；报告与快照的 sha256 对得上。
能保证：叙述里的实测数字来自快照（手敲数字的实测句会被判 FAIL）；每个含数字/全称/否定词的句子都带标签；「没有 X」都带扫描范围。
不能保证：**标了转述的句子是否真有出处**（这是回归里最大的剩余风险，计划用来源台账解决）；解释性的推论是否站得住；HTML 在生成后有没有被手改；独立人工验收要另起子代理做，没有自动化；`--live` 只抽查前 6 个合约的指数成分和成交量级。

## 它不能做什么

- 不覆盖其他交易所。
- 拿不到杠杆分档、维持保证金率（需登录）；标记价公式、休市定价、监管主体、托管人等要靠公告，读不到就标「未证实」。
- 周末窗口里股票/ETF 的 20:00 ET 是假设；平日基准不排除美股节假日；时段统计是美股时段，对商品和外汇没有意义。
- 取数是串行的、没有重试和限流退避：index 类别 24 个合约加现货约要 5 分钟。
- 币安现货端点遇到 451/403 时没有专门处理，只会记入 missing。
- 指数观察清单和 bStocks 自动配对是按代号识别，不是官方清单。
- 不构成投资建议。

## 测试

```bash
python3 -m unittest discover -s tests
```

覆盖：深度带计算、资金费率统计、夏令时/冬令时的周末窗口、周末与时段统计、价差统计、生成与验收流水线（含"快照被篡改必须不合格"）、HTML 转义、缺字段不崩、取数失败路径（用桩，不联网）。**没有覆盖**：真实联网取数的端到端（需手动跑）、`--live` 抽查逻辑本身、成稿里手敲数字（验收器查不出）。

## 目录

```
SKILL.md                      skill 入口（规则与流程）
scripts/                      取数 / 生成 / 验收
config/index_watchlist.json   指数类合约观察清单
templates/style.css           报告样式（深色模式）
references/                   方法、接口、坑、验收提示词、叙述模板
examples/                     四份 v0.1 手工版报告（数字已过期）+ derivatives-v2/（v0.2 流程产出的回归报告与叙述稿）
docs/                         回归记录
tests/                        单元测试
```
