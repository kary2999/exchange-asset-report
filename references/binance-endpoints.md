# 用到的币安公开接口（无需 API key）

| 用途 | 接口 | 备注 |
|---|---|---|
| 合约清单与类型 | `GET fapi/v1/exchangeInfo` | `contractType`、`underlyingType`、`underlyingSubType`、`onboardDate`、`filters` |
| 24h 行情 | `GET fapi/v1/ticker/24hr?symbol=` | `quoteVolume`、`priceChangePercent` |
| 指数/标记价/资金费率 | `GET fapi/v1/premiumIndex?symbol=` | `interestRate` 为 0 说明费率里没有利差项 |
| 未平仓 | `GET fapi/v1/openInterest?symbol=` | 单位是币数，乘标记价得美元 |
| **指数成分** | `GET fapi/v1/constituents?symbol=` | 价格来源的关键证据 |
| 资金费率历史 | `GET fapi/v1/fundingRate?symbol=&limit=` | |
| 费率周期/上下限 | `GET fapi/v1/fundingInfo` | 商品 4h，股票/ETF/外汇 8h |
| 盘口深度 | `GET fapi/v1/depth?symbol=&limit=1000` | 深度带按中间价 ±x% 累加 |
| K 线 | `GET fapi/v1/klines?symbol=&interval=1h&limit=` | 第 8 列 quoteVolume，第 9 列成交笔数 |
| 现货行情/深度/K 线/清单 | `api.binance.com/api/v3/ticker/24hr`、`depth`、`klines`、`exchangeInfo?permissions=SPOT` | bStocks、PAXG、USDTBRL |
| 期权清单 | `GET eapi/v1/exchangeInfo` | `optionContracts[].nakedSell`、`optionSymbols[].underlying` |
| 期权标记价/IV/希腊值 | `GET eapi/v1/mark?symbol=` | |
| 需要登录（本 skill 拿不到） | `leverageBracket`（杠杆分档） | 杠杆倍数只能写"以 App 为准" |

## 注意
- 接口返回 HTTP 451/403 表示地区受限：停止，不要估算。
- 币安 `exchangeInfo` 里 `requiredMarginPercent` 不能当作最大杠杆（商品新闻称 50~100 倍，接口里仍是 5%）。
- `constituents` 的 `price` 字段是 -1，只有 `weight` 可用。
