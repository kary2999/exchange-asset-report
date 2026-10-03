---
name: exchange-asset-report
description: |
  【币安 TradFi / 代币化资产 / 衍生品业务调研报告生成】
  对币安的美股/ETF 永续、bStocks 代币化证券、大宗商品、外汇、指数、CFD、期货、期权等做"价格来源 + 生产来源 + 玩法 + 实测"的白话调研，输出 HTML 报告。
  数字由脚本从币安公开接口取数并自动填表（叙述里的数字也用内联占位取数，不手敲）；每个事实句必须带可信度标签（实测/转述/未证实/推论）；"没有 X"只能写成"在某扫描范围里没找到"；拿不到就写"数据缺失"。
  触发场景（模糊匹配）：
  - 币安/Binance + 股票永续、TradFi 永续、bStocks、代币化股票、美股期权、CFD、指数、ETF、期货、黄金/白银/原油/外汇永续
  - "调研 xx 的价格来源 / 谁发行的 / 怎么玩 / 盘后/周末行情 / 深度和成交"
  - "小交易所依托币安做 xx 要满足什么条件"（这部分只能是网页转述，见规则 8）
  不触发：其他交易所（本 skill 只验证过币安）、纯加密币种分析、给投资建议。
---

# exchange-asset-report（币安版 v0.2）

只覆盖币安。别的交易所的接口和指数字段不同，没验证过，不要声称通用。

## 硬性规则（`check_report.py` 会逐句强制其中大部分，违反即 FAIL）

1. **不编造数字。** 数字只来自 snapshot。表格用 `<!--AUTO:xxx-->`，叙述里的数字用内联占位 `{{i:survey.fapi.total}}`、`{{v:BZUSDT.weight[hyperliquid]|pct}}`，**不要手敲**。接口拿不到的写"数据缺失"。
2. **每个含数字、全称或否定词的句子必须带标签：** `{V}` 实测（来自快照）、`{G}` 转述（公告/新闻稿/媒体）、`{U}` 未证实、`{I}` 推论。解释现象、猜原因一律 `{I}`。第三方站（教程、博客）的内容只能 `{U}`。
3. **`{V}` 句的条件：** 不得含推测措辞（我判断/通常/可能…）；不得含手敲数字；含"没有/只有/全部/都是"时必须有 `{{...}}` 快照数字做证据。
4. **「没有 X」只能写成「在 <扫描范围> 里没有找到 X」。** 范围取自 `<!--AUTO:scope-->`（实际扫过的接口与数量）；表里没列的地方（App 页面、公告、各地区站点、其他入口）一律算没扫。不得写"币安没有 X""全球站没有"。
5. **标注时间。** 快照时间自动写在页首；美股时段用 ET，加密用 UTC。事实会过期，不要写死"币安只有 X 个"。
6. **不给投资建议。** 不替用户点协议、答风险题、下单。
7. **连通性失败就停。** 取数脚本启动时先打 `fapi exchangeInfo`，失败（含 451/403）直接退出，不降级成估算。启动后个别接口失败写入 `snapshot["missing"]`，对应单元格显示「数据缺失」；脚本主动跳过的（如非 TradFi 合约不算休市窗口）显示「不适用：原因」。
8. **范围声明 + 网页转述区。** 报告必须有 `id="coverage"` 的范围声明，写明哪些部分有脚本数据、哪些只是网页转述。题目超出脚本覆盖的内容（CFD、交割期货、准入/商务条件、监管主体）整体放进 `<div class="webonly">…</div>`：区内不允许出现 `{V}`，默认 `{G}`/`{U}`。
9. **叙述稿是唯一来源。** 报告永远由 `build_report` 从 narrative 重建；任何修改只改 narrative 再重建；`check_report --narrative` 校验哈希。

## 流程

```
0 确认范围 → 1 取数 → 2 查资料 → 3 写 narrative → 4 生成 → 5 机器验收 → 6 独立验收 → 7 交付
```

### 0 确认范围
问一个关键问题：要哪一类（`survey` 全品类盘点 / `commodity` / `fx` / `index` / `equity`，或指定 symbols）？是否要现货对照（bStocks、PAXG、USDTBRL）？题目里哪些部分超出脚本覆盖（CFD、期货、准入条件）？这些要进转述区。

### 1 取数（确定性，必须用脚本）
```bash
python3 scripts/binance_data.py --category survey --out snap_survey.json      # 全品类盘点（不逐合约取数）
python3 scripts/binance_data.py --category commodity --pairs PAXGUSDT:XAUUSDT --out snap_com.json
python3 scripts/binance_data.py --category fx --pairs USDTBRL:USDBRLUSDT --out snap_fx.json
python3 scripts/binance_data.py --category index --auto-bstocks --out snap_idx.json
python3 scripts/binance_data.py --category equity --symbols NVDAUSDT --pairs NVDABUSDT:NVDAUSDT --out snap_nvda.json
```
每个快照都带 `survey` 盘点：U 本位合约类型/状态/保证金币种/INDEX 合约清单/交割合约/SETTLING 计数/成交分桶，币本位合约，现货，币安期权标的，**以及扫描范围声明**。per-symbol：24h 成交/未平仓、价差、±0.1/0.5/1% 深度、指数成分与权重、资金费率统计、1 小时 K 线、周末与时段统计（仅 TRADIFI 永续）、现货对照价差。期权清单（commodity）、法币对（fx）、bStocks 候选（index）按类别取。
取数完先看 `missing=`，不为 0 就读 `snapshot["missing"]`。取数是串行的，index 类别约 5 分钟。

### 2 查资料（不确定性，网页）
读币安公告/新闻稿、FAQ/学院、权威媒体，标 `{G}`。读不到原文（403/404/动态页）写 `{U}`，**不要用搜索摘要里没出处的句子**；搜索摘要常混入别家产品（例如把 Kraken xStocks 的托管信息安到 bStocks 上）。详见 `references/method.md`。

### 3 写 narrative
复制 `references/narrative-template.html`，按 `GUIDE` 注释写。可用占位：
- 表格：`meta`、`scope`、`survey`、`volume`、`inventory`、`contracts`、`constituents[:SYM+SYM]`、`funding`、`weekend`、`sessions`、`basis`、`options`、`fiatpairs`、`bstocks`、`missing`
- 内联取数：`{{i:路径|格式}}`（盘点，路径以 `survey.`/`inventory.` 开头）、`{{v:合约.字段|格式}}`（合约字段，权重用 `weight[来源名]`）、`{{s:现货对.字段|格式}}`（现货对照，如 `{{s:PAXGUSDT.basis_7d.weekend_mean_bp|bp}}`）；格式：`usd` `pct` `pctn` `bp` `int` `f1` `f2` `len`

### 4 生成
```bash
python3 scripts/build_report.py --snapshot snap_survey.json --snapshot snap_com.json --narrative narrative.html \
  --title "币安衍生品业务调研" --out report.html
```

### 5 机器验收
```bash
python3 scripts/check_report.py report.html --snapshot snap_survey.json --snapshot snap_com.json --narrative narrative.html --live
```
检查：标签平衡、占位符、必备章节与范围声明、断言级规则（见 `scripts/claims.py`）、快照与叙述稿哈希、繁体混入、`--live` 重抓指数成分结构。FAIL 必须清零；WARN 逐条看。
**机检过 ≠ 内容对。** 回归夹具里 18 处真实错误，机检直接判 FAIL 13 处、提醒 2 处，其余 3 处靠独立验收员或源头改进。

### 6 独立验收
起一个独立子代理，提示词见 `references/verifier-prompt.md`。验收员**只改 narrative**，再重建、再跑 check。这一步在两次实战里都抓出过机检抓不到的问题，不能省。

### 7 交付
给用户：报告路径、一句话结论、仍然"没查到"的清单、验收员改了什么。如实说跳过了什么。

## 目录

- `scripts/binance_data.py` 取数；`build_report.py` 生成；`check_report.py` 验收；`claims.py` 断言级分析
- `config/index_watchlist.json` 指数类合约观察清单（代号识别，非官方）
- `references/` 方法、接口、坑、验收提示词、叙述模板
- `examples/` 四份第一版的手工报告（数字已过期，且不符合 v0.2 的写作规则）
- `tests/` 单元与回归测试：`python3 -m unittest discover -s tests`
