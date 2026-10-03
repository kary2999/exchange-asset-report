---
name: exchange-asset-report
description: |
  【币安 TradFi / 代币化资产调研报告生成】
  对币安的美股/ETF 永续、bStocks 代币化证券、大宗商品、外汇、指数类产品做"价格来源 + 生产来源 + 玩法 + 实测"的白话调研，输出 HTML 报告。
  数字由脚本从币安公开接口取数并自动填表，不手抄；每个结论标可信度（实测/转述/未证实/推论）；拿不到就写"数据缺失"。
  触发场景（模糊匹配）：
  - 币安/Binance + 股票永续、TradFi 永续、bStocks、代币化股票、美股期权、黄金/白银/原油永续、外汇永续、指数/ETF 永续
  - "调研 xx 的价格来源 / 谁发行的 / 怎么玩 / 盘后/周末行情 / 深度和成交"
  - "把 xx 做成一篇白话报告 / 对比现货和永续"
  不触发：其他交易所（本 skill 只验证过币安）、纯加密币种分析、给投资建议。
---

# exchange-asset-report（币安版 v0.1）

只覆盖币安。别的交易所的接口和指数字段不同，没验证过，不要声称通用。

## 硬性规则（违反即不合格）

1. **不编造数字。** 数字只来自 `snapshot.json`（脚本取数）。接口拿不到的写"数据缺失"，并在"没查到的"一节说明。
2. **每个事实带可信度标签**：`{V}`实测（接口直接拿到）、`{G}`转述（公告/新闻稿/媒体，没对原文）、`{U}`未证实、`{I}`推论（我从数据推的）。解释现象的话一律 `{I}`。
3. **标注时间。** 快照时间写进报告，美股时段用 ET，加密用 UTC。行情数字会漂移，正文引用量级即可。
4. **事实会过期。** 杠杆倍数、合约数量、上线品种、监管主体不要写死成"币安只有 X 个"，写"截至快照时间"。
5. **不给投资建议。** 不替用户点协议、答风险题、下单。
6. **连通性失败就停。** 取数脚本启动时先打 `fapi exchangeInfo`，失败（含 451/403）直接退出，不降级成估算。启动后个别接口（现货、期权、单个合约）失败不会中止，而是写入 `snapshot["missing"]`，对应单元格显示「数据缺失」。

## 流程

```
0 确认范围 → 1 取数 → 2 查资料 → 3 写 narrative → 4 生成 → 5 验收 → 6 交付
```

### 0 确认范围
问一个关键问题：要哪一类（`commodity` / `fx` / `index` / `equity`，或指定 symbols）？是否要现货对照（bStocks、PAXG、USDTBRL）？

### 1 取数（确定性，必须用脚本）
```bash
python3 scripts/binance_data.py --category commodity --pairs PAXGUSDT:XAUUSDT --out snap_com.json
python3 scripts/binance_data.py --category fx --pairs USDTBRL:USDBRLUSDT --out snap_fx.json
python3 scripts/binance_data.py --category index --auto-bstocks --out snap_idx.json
python3 scripts/binance_data.py --category equity --symbols NVDAUSDT --pairs NVDABUSDT:NVDAUSDT --out snap_nvda.json
```
脚本取：合约清单与类型、24h 成交/未平仓、买一卖一价差、±0.1/0.5/1% 深度、指数成分与权重、资金费率周期/上下限/统计、1 小时 K 线、周末窗口与时段统计（仅 TRADIFI 永续；加密币永续会跳过）、现货对照价差。仅特定类别才取：期权清单（commodity）、法币对数量（fx）、bStocks 候选（index）。
取数完先看脚本输出的 `missing=`，不为 0 就读 `snapshot["missing"]`。

### 2 查资料（不确定性，网页）
读这些并标 `{G}`：币安公告/新闻稿（上线、监管主体、杠杆、休市规则）、币安 FAQ/学院、权威媒体。读不到原文（403/404/动态页）就写 `{U}`，**不要用搜索摘要里没出处的句子**。搜索摘要常混入别家产品（例如把 Kraken xStocks 的托管信息安到 bStocks 上），出现这类情况要明说。详见 `references/method.md`。

### 3 写 narrative
复制 `references/narrative-template.html`，按其中 `GUIDE` 注释写。表格用 `<!--AUTO:xxx-->` 占位，**不要手敲任何行情数字**；正文需要引用数字时，只引用自动表格里已出现的值。

可用占位：`meta`、`inventory`、`contracts`、`constituents[:SYM+SYM]`、`funding`、`weekend`、`sessions`、`basis`、`options`、`fiatpairs`、`bstocks`、`missing`。

### 4 生成
```bash
python3 scripts/build_report.py --snapshot snap_com.json --narrative narrative.html \
  --title "币安大宗商品玩法拆解" --out report.html
```
多个快照可重复传 `--snapshot`。生成后报告头部带 `snapshot-sha256`。

### 5 验收（两层，都要做）
```bash
python3 scripts/check_report.py report.html --snapshot snap_com.json --live
```
机器验收：标签平衡、占位符、必备章节、标签使用、繁体混入、快照 sha 是否一致、`--live` 重抓指数成分结构。
**再起一个独立子代理做人工验收**，提示词见 `references/verifier-prompt.md`。这一步在第一次实战里抓出过单位错误（成交额差 10 倍）和汇总数字错误，不能省。验收员发现的结构性错误要改，漂移不用改。

### 6 交付
给用户：报告路径、一句话结论、仍然"没查到"的清单。如实说跳过了什么。

## 已知坑（来自实战）

见 `references/pitfalls.md`。最重要的三条：单位要在每个单元格自带（"$1.50亿"、"$526万"），不要靠表头；平日基准要写清定义；"周末资金费率为 0"是观察，原因是推论。

## 目录

- `scripts/binance_data.py` 取数；`scripts/build_report.py` 生成；`scripts/check_report.py` 验收
- `config/index_watchlist.json` 指数类合约观察清单（代号识别，非官方）
- `references/` 方法、接口说明、坑、验收提示词、叙述模板
- `examples/` 四份实战报告（手工版，数字是当时快照；新流程产出的报告数字由脚本填入）
- `tests/` 单元测试：`python3 -m unittest discover -s tests`
