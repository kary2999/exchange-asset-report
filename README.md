# exchange-asset-report

用币安公开接口取数（官方公告需人工/模型另查，脚本不抓公告），生成「价格从哪来 / 谁生产的 / 怎么玩 / 实测」的白话 HTML 调研报告。当前版本只覆盖**币安**（美股/ETF 永续、bStocks、大宗、外汇、指数类产品）。

核心思路：**数字交给脚本，判断交给人，验收交给另一个人。**

| 层 | 做什么 | 确定性 |
|---|---|---|
| `scripts/binance_data.py` | 取数：合约清单、成交、深度、指数成分、资金费率、K 线、周末/盘后统计、现货对照 | 确定，可重复 |
| `scripts/build_report.py` | 快照 + 叙述模板 -> HTML，表格自动填，缺字段显示「数据缺失」 | 确定 |
| `scripts/check_report.py` | 验收：HTML 结构、占位符、必备章节、标签、繁体混入、快照 sha 一致、`--live` 抽查 | 确定 |
| `SKILL.md` + `references/` | 方法、规则、坑、独立验收员提示词（Claude Code skill） | 需要人/模型判断 |

## 安装为 Claude Code skill

```bash
git clone https://github.com/kary2999/exchange-asset-report.git
ln -s "$(pwd)/exchange-asset-report" ~/.claude/skills/exchange-asset-report
```

## 单独使用脚本（只需 Python 3.9+，无第三方依赖）

```bash
# 1 取数
python3 scripts/binance_data.py --category commodity --pairs PAXGUSDT:XAUUSDT --out snap.json
# 2 复制 references/narrative-template.html 写叙述，用 <!--AUTO:contracts--> 等占位
# 3 生成
python3 scripts/build_report.py --snapshot snap.json --narrative narrative.html --title "币安大宗商品玩法拆解" --out report.html
# 4 验收
python3 scripts/check_report.py report.html --snapshot snap.json --live
```

类别：`commodity`、`fx`、`index`（读 `config/index_watchlist.json`）、`equity`、`custom`（配 `--symbols`）。

## 它能保证什么、不能保证什么

能保证：自动表格里的数字来自快照；缺字段显示「数据缺失」；接口返回的字符串已转义；报告与快照的 sha256 对得上。
不能保证：叙述里手敲的数字对不对（验收只会提醒，不能证明）；HTML 在生成后有没有被手改；独立人工验收要另起子代理做，没有自动化；`--live` 只抽查前 6 个合约的指数成分和成交量级。

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
examples/                     四份手工版实战报告（数字已过期）
tests/                        单元测试
```
