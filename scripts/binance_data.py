#!/usr/bin/env python3
"""币安 TradFi 数据取数脚本：公开接口 -> snapshot.json（仅标准库，Python 3.9+）。

原则：
- 只写接口真的返回的数据。拿不到的字段写入 snapshot["missing"]，绝不估算或补全。
- 每个快照带 fetched_at_utc 和用到的接口清单，成稿时必须原样引用。
- 连通性检查失败（例如 HTTP 451 地区限制）直接退出，不降级。
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

VERSION = "0.2.0"
FAPI = "https://fapi.binance.com/fapi/v1/"
FDATA = "https://fapi.binance.com/futures/data/"
DAPI = "https://dapi.binance.com/dapi/v1/"
SPOT = "https://api.binance.com/api/v3/"
EAPI = "https://eapi.binance.com/eapi/v1/"
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

FIAT = {"EUR", "GBP", "BRL", "TRY", "ARS", "MXN", "JPY", "AUD", "PLN", "RON", "UAH", "CZK", "COP",
        "ZAR", "NGN", "RUB", "IDR", "AED", "KZT", "PEN", "CLP", "CHF", "CAD", "INR", "VND", "KRW"}
INDEX_NAME_PATTERN = re.compile(
    r"NAS|SPX|US500|US100|NDX|DJI|DAX|HSI|N225|NIKKEI|FTSE|VIX|DXY|USDX|JP225|HK50|KOSPI|SP500|SPX500|US30",
    re.I)

# 各类资产的「休市窗口」(ET)。(周五起始, 周日结束)。equity 的 20:00 是美股 24/5 夜盘结束，属于假设，成稿须标 推论。
WEEKEND_RULES = {
    "commodity": ((17, 0), (18, 0)),
    "fx": ((17, 0), (17, 0)),
    "equity": ((20, 0), (20, 0)),
    "index": ((20, 0), (20, 0)),
}


class Fetcher:
    def __init__(self):
        self.used = []
        self.missing = []

    def get(self, url, note="", retries=2):
        """网络异常、HTTP 429/418/5xx 重试 retries 次（退避 1.5s、3s）；其他 4xx（400/403/404/451）不重试。"""
        base = url.split("?")[0]
        if base not in self.used:
            self.used.append(base)
        err = ""
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                err = "HTTP %s" % e.code
                if e.code not in (418, 429) and e.code < 500:
                    break
            except Exception as e:  # 网络、超时、解析
                err = str(e)[:200]
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        self.missing.append({"url": url, "error": err, "note": note})
        return None


def check_connectivity(f):
    d = f.get(FAPI + "exchangeInfo", "connectivity")
    if d is None:
        err = f.missing[-1]["error"] if f.missing else "unknown"
        sys.exit("连通性检查失败（%s）。币安接口不可达或本地区受限，停止，不会用估算数据代替。" % err)
    return d


# ---------- 纯计算（可单测） ----------

def fnum(x):
    """接口数字字段 -> float；缺失/空串/非数字返回 None（例如新合约 lastFundingRate 为 ""）。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def weekend_kind_for(underlying_type):
    """休市窗口按合约自己的 underlyingType 选，而不是按命令行 category（custom/混合 symbols 时 category 不可靠）。
    非商品/外汇一律按股票/ETF 窗口（20:00 ET，属假设）。"""
    return {"COMMODITY": "commodity", "FX": "fx"}.get(underlying_type, "equity")


def usd_depth_bands(bids, asks, pcts=(0.1, 0.5, 1.0)):
    """bids/asks: [[price, qty], ...]（字符串或数字）。返回 (spread_bp, {pct: [bid_usd, ask_usd]})。"""
    bb, aa = float(bids[0][0]), float(asks[0][0])
    mid = (bb + aa) / 2
    spread_bp = (aa - bb) / mid * 1e4
    out = {}
    for p in pcts:
        b = sum(float(x) * float(q) for x, q in bids if float(x) >= mid * (1 - p / 100))
        a = sum(float(x) * float(q) for x, q in asks if float(x) <= mid * (1 + p / 100))
        out[str(p)] = [round(b), round(a)]
    return round(spread_bp, 3), out


def funding_stats(history):
    """history: fundingRate 接口返回的列表。"""
    rates = [float(x["fundingRate"]) for x in history]
    if not rates:
        return None
    nz = [r for r in rates if r != 0]
    zero_times = []
    weekend_zero = 0
    for x in history:
        t = dt.datetime.fromtimestamp(x["fundingTime"] / 1000, UTC)
        if float(x["fundingRate"]) == 0:
            zero_times.append(t.strftime("%a%H"))
            if t.weekday() >= 5:
                weekend_zero += 1
    return {
        "n": len(rates), "zero_n": len(rates) - len(nz), "weekend_zero_n": weekend_zero,
        "mean_pct": round(st.mean(rates) * 100, 5),
        "nonzero_mean_pct": round(st.mean(nz) * 100, 5) if nz else None,
        "min_pct": round(min(rates) * 100, 5), "max_pct": round(max(rates) * 100, 5),
        "sum_pct": round(sum(rates) * 100, 4),
        "zero_times_utc": zero_times[:24],
    }


def weekend_window(now_utc, kind):
    """返回上一个已结束的休市窗口 (start_utc, end_utc)。窗口 = 周五 (h,m) ET 到 周日 (h,m) ET。"""
    (fh, fm), (sh, sm) = WEEKEND_RULES[kind]
    now_et = now_utc.astimezone(ET)
    # 找最近一个周日 sh:sm（已过去）
    d = now_et.date()
    for back in range(0, 15):
        day = d - dt.timedelta(days=back)
        if day.weekday() == 6:
            end = dt.datetime(day.year, day.month, day.day, sh, sm, tzinfo=ET)
            if end <= now_et - dt.timedelta(hours=1):
                fri = day - dt.timedelta(days=2)
                start = dt.datetime(fri.year, fri.month, fri.day, fh, fm, tzinfo=ET)
                return start.astimezone(UTC), end.astimezone(UTC)
    raise RuntimeError("找不到已结束的周末窗口")


def _row(k):
    return {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]),
            "qv": float(k[7]), "n": int(k[8])}


def weekend_stats(rows, start_utc, end_utc):
    """rows: 1h K 线（_row 格式）。平日=数据内所有完整的 UTC 周一到周四。"""
    def T(r):
        return dt.datetime.fromtimestamp(r["t"] / 1000, UTC)
    wk = [r for r in rows if start_utc <= T(r) < end_utc]
    byday = {}
    for r in rows:
        t = T(r)
        if t.weekday() <= 3:
            byday.setdefault(t.date(), []).append(r)
    days = [v for v in byday.values() if len(v) == 24]
    if not wk or not days:
        return None
    wd = [r for v in days for r in v]
    rng = lambda rs: (max(r["h"] for r in rs) - min(r["l"] for r in rs)) / rs[0]["o"] * 100
    wk_vol = st.mean(r["qv"] for r in wk)
    wd_vol = st.mean(r["qv"] for r in wd)
    if wd_vol == 0 or any(r["o"] == 0 for r in wk) or any(v[0]["o"] == 0 for v in days):
        return None  # 平日零成交/零价格：比值无意义，不输出
    pre = [r for r in rows if T(r) == start_utc - dt.timedelta(hours=1)]
    aft = [r for r in rows if T(r) >= end_utc]
    gap = None
    if pre and aft:
        gap = (aft[0]["o"] / pre[0]["c"] - 1) * 100
    return {
        "window_utc": [start_utc.strftime("%Y-%m-%d %H:%M"), end_utc.strftime("%Y-%m-%d %H:%M")],
        "weekend_hours": len(wk), "weekday_days": len(days),
        "weekend_vol_per_h": round(wk_vol), "weekday_vol_per_h": round(wd_vol),
        "vol_ratio_pct": round(wk_vol / wd_vol * 100, 1),
        "weekend_range_pct": round(rng(wk), 3),
        "weekday_avg_daily_range_pct": round(st.mean(rng(v) for v in days), 3),
        "reopen_gap_pct": None if gap is None else round(gap, 3),
        "weekend_first_price": wk[0]["o"], "weekend_last_price": wk[-1]["c"],
    }


def session_stats(rows):
    """按美东时间分桶（仅 ET 周一到周五）：开盘小时 / 盘中 / 盘后 / 夜盘盘前。每小时均值。"""
    buckets = {"open_hour(09-10 ET)": [], "regular(10-16 ET)": [], "after_hours(16-20 ET)": [], "overnight_pre(20-09 ET)": []}
    for r in rows[:-1]:  # 最后一根可能未走完，丢弃
        t = dt.datetime.fromtimestamp(r["t"] / 1000, UTC).astimezone(ET)
        if t.weekday() >= 5:
            continue
        h = t.hour
        if h == 9:
            key = "open_hour(09-10 ET)"
        elif 10 <= h < 16:
            key = "regular(10-16 ET)"
        elif 16 <= h < 20:
            key = "after_hours(16-20 ET)"
        else:
            key = "overnight_pre(20-09 ET)"
        buckets[key].append(r)
    out = {}
    for k, rs in buckets.items():
        if rs:
            out[k] = {"n_hours": len(rs), "vol_per_h": round(st.mean(r["qv"] for r in rs)),
                      "range_pct": round(st.mean((r["h"] - r["l"]) / r["o"] * 100 for r in rs), 3),
                      "trades_per_h": round(st.mean(r["n"] for r in rs))}
    return out


def basis_stats(spot_rows, perp_rows, wk=None):
    pd = {r["t"]: r["c"] for r in perp_rows}
    allb, wkb = [], []
    for r in spot_rows:
        if r["t"] in pd:
            b = (r["c"] / pd[r["t"]] - 1) * 1e4
            allb.append(b)
            if wk:
                t = dt.datetime.fromtimestamp(r["t"] / 1000, UTC)
                if wk[0] <= t < wk[1]:
                    wkb.append(b)
    if not allb:
        return None
    return {"n": len(allb), "mean_bp": round(st.mean(allb), 2), "min_bp": round(min(allb), 2),
            "max_bp": round(max(allb), 2), "weekend_mean_bp": round(st.mean(wkb), 2) if wkb else None}


NOT_SCANNED = ["币安 App / 官网页面", "币安公告与监管文件全文", "币安 Margin、Alpha、Convert、P2P、Earn 等其他入口",
               "不同地区站点上的 CFD / 经纪商业务页面", "币安白标、Link 经纪商的商务条款（需向币安商务确认）"]


def _count(items, key):
    d = {}
    for x in items:
        k = x.get(key) or "?"
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def build_survey(f, fapi_ex):
    """全面盘点：不只看 TradFi。每个「没有 X」的结论只能引用这里实际扫过的范围。
    返回 (survey, spot_info, eapi_info)。拿不到的部分记入 f.missing 并在 scope 里写明。"""
    syms = fapi_ex["symbols"]
    tradfi = [x for x in syms if x.get("contractType") == "TRADIFI_PERPETUAL"]
    settling = [x for x in syms if x.get("status") == "SETTLING"]
    fapi = {
        "total": len(syms), "by_contract_type": _count(syms, "contractType"), "by_status": _count(syms, "status"),
        "by_underlying_type": _count(syms, "underlyingType"),
        "tradfi_total": len(tradfi), "tradfi_by_underlying_type": _count(tradfi, "underlyingType"),
        "tradfi_by_status": _count(tradfi, "status"), "tradfi_margin_assets": _count(tradfi, "marginAsset"),
        "settling_total": len(settling), "settling_by_contract_type": _count(settling, "contractType"),
        "tradfi_symbols": {},
        "schema": {"symbol_keys": sorted(tradfi[0].keys()) if tradfi else [],
                   "filter_types": sorted({f_["filterType"] for x in tradfi[:20] for f_ in x.get("filters", [])}) if tradfi else []},
        "index_contracts": [{"symbol": x["symbol"], "status": x.get("status"), "contractType": x.get("contractType"),
                             "subtypes": x.get("underlyingSubType", [])} for x in syms if x.get("underlyingType") == "INDEX"],
        "delivery_contracts": [{"symbol": x["symbol"], "contractType": x["contractType"], "underlyingType": x.get("underlyingType")}
                               for x in syms if x.get("contractType") in ("CURRENT_QUARTER", "NEXT_QUARTER")],
        "name_scan": [{"symbol": x["symbol"], "underlyingType": x.get("underlyingType")}
                      for x in syms if INDEX_NAME_PATTERN.search(x["symbol"])],
    }
    for x in tradfi:
        fapi["tradfi_symbols"].setdefault(x.get("underlyingType", "?"), []).append({
            "symbol": x["symbol"], "subtypes": x.get("underlyingSubType", []),
            "onboard": dt.datetime.fromtimestamp(x["onboardDate"] / 1000, UTC).strftime("%Y-%m-%d") if x.get("onboardDate") else ""})
    scope = [{"endpoint": FAPI + "exchangeInfo", "scanned": "U 本位合约（合约类型、标的类型、状态、保证金币种、名称扫描）", "count": len(syms)}]
    t24 = f.get(FAPI + "ticker/24hr", "bulk ticker 24h")
    if isinstance(t24, list):
        qv = {x["symbol"]: fnum(x.get("quoteVolume")) for x in t24}
        vols = [(x["symbol"], qv.get(x["symbol"])) for x in tradfi if qv.get(x["symbol"]) is not None]
        edges = [("<10万", 1e5), ("10万~100万", 1e6), ("100万~1000万", 1e7), ("1000万~1亿", 1e8), (">=1亿", float("inf"))]
        buckets, lo = {}, 0.0
        for name, hi in edges:
            buckets[name] = sum(1 for _, v in vols if lo <= v < hi)
            lo = hi
        fapi["tradfi_volume_24h"] = {
            "n": len(vols), "total_usd": sum(v for _, v in vols), "buckets": buckets,
            "below_1m_n": sum(1 for _, v in vols if v < 1e6),
            "top10": [{"symbol": a, "usd": b} for a, b in sorted(vols, key=lambda x: -x[1])[:10]]}
        scope.append({"endpoint": FAPI + "ticker/24hr", "scanned": "全部 U 本位合约 24h 成交额", "count": len(t24)})
    out = {"fapi": fapi}

    dp = f.get(DAPI + "exchangeInfo", "dapi exchangeInfo")
    if dp and dp.get("symbols"):
        ds = dp["symbols"]
        out["dapi"] = {"total": len(ds), "by_contract_type": _count(ds, "contractType"),
                       "by_underlying_type": _count(ds, "underlyingType"),
                       "name_scan": [{"symbol": x["symbol"], "underlyingType": x.get("underlyingType")}
                                     for x in ds if INDEX_NAME_PATTERN.search(x["symbol"])]}
        scope.append({"endpoint": DAPI + "exchangeInfo", "scanned": "币本位合约（合约类型、标的类型、名称扫描）", "count": len(ds)})
    else:
        scope.append({"endpoint": DAPI + "exchangeInfo", "scanned": "币本位合约：本次取数失败，未扫描", "count": None})

    spot_info = f.get(SPOT + "exchangeInfo?permissions=SPOT", "spot exchangeInfo")
    if spot_info and spot_info.get("symbols"):
        sp = [x for x in spot_info["symbols"] if x.get("status") == "TRADING"]
        quoted, base = {}, {}
        for x in sp:
            if x["quoteAsset"] in FIAT:
                quoted[x["quoteAsset"]] = quoted.get(x["quoteAsset"], 0) + 1
            if x["baseAsset"] in FIAT:
                base.setdefault(x["baseAsset"], []).append(x["symbol"])
        out["spot"] = {"trading_symbols": len(sp),
                       "name_scan": [{"symbol": x["symbol"]} for x in sp if INDEX_NAME_PATTERN.search(x["symbol"])],
                       "fiat_quoted": dict(sorted(quoted.items(), key=lambda kv: -kv[1])),
                       "fiat_as_base": {k: v[:6] for k, v in base.items()}}
        scope.append({"endpoint": SPOT + "exchangeInfo?permissions=SPOT", "scanned": "现货交易对（名称扫描、法币计价交易对）", "count": len(sp)})
    else:
        spot_info = None
        scope.append({"endpoint": SPOT + "exchangeInfo", "scanned": "现货：本次取数失败，未扫描", "count": None})

    eapi_info = f.get(EAPI + "exchangeInfo", "options exchangeInfo")
    if eapi_info and eapi_info.get("optionContracts") is not None:
        out["eapi"] = {"underlyings": [c["underlying"] for c in eapi_info["optionContracts"]],
                       "listed_count": _count(eapi_info.get("optionSymbols", []), "underlying"),
                       "naked_sell": {c["underlying"]: c["nakedSell"] for c in eapi_info["optionContracts"]}}
        scope.append({"endpoint": EAPI + "exchangeInfo", "scanned": "币安自己的期权（标的清单、挂牌合约数）。不含走 Alpaca 的美股期权",
                      "count": len(eapi_info.get("optionSymbols", []))})
    else:
        eapi_info = None
        scope.append({"endpoint": EAPI + "exchangeInfo", "scanned": "期权：本次取数失败，未扫描", "count": None})
    out["scope"] = scope
    out["not_scanned"] = NOT_SCANNED
    return out, spot_info, eapi_info


# ---------- 取数 ----------

def fetch_symbol(f, sym, funding_n, kline_n, onboard_ms=None):
    out = {"symbol": sym}
    t = f.get(FAPI + "ticker/24hr?symbol=" + sym, "ticker")
    p = f.get(FAPI + "premiumIndex?symbol=" + sym, "premiumIndex")
    oi = f.get(FAPI + "openInterest?symbol=" + sym, "openInterest")
    if t:
        for key, field in (("quote_volume_24h", "quoteVolume"), ("price_change_pct_24h", "priceChangePercent"),
                           ("last_price", "lastPrice")):
            v = fnum(t.get(field))
            if v is not None:
                out[key] = v
    if p:
        mk, ix = fnum(p.get("markPrice")), fnum(p.get("indexPrice"))
        if mk is not None:
            out["mark"] = mk
        if ix is not None:
            out["index"] = ix
        if mk is not None and ix:
            out["mark_vs_index_bp"] = round((mk / ix - 1) * 1e4, 2)
        lf = fnum(p.get("lastFundingRate"))  # 新合约可能是 ""
        if lf is not None:
            out["last_funding_rate_pct"] = round(lf * 100, 5)
        ir = fnum(p.get("interestRate"))
        if ir is not None:
            out["interest_rate"] = ir
        oiq = fnum(oi.get("openInterest")) if oi else None
        if oiq is not None and mk is not None:
            out["open_interest_usd"] = oiq * mk
    d = f.get(FAPI + "depth?symbol=%s&limit=1000" % sym, "depth")
    if d and d.get("bids") and d.get("asks"):
        out["spread_bp"], out["depth_usd"] = usd_depth_bands(d["bids"], d["asks"])
    c = f.get(FAPI + "constituents?symbol=" + sym, "constituents")
    if isinstance(c, dict) and c.get("constituents"):
        try:
            out["constituents"] = [{"exchange": x["exchange"], "symbol": x["symbol"], "weight": float(x["weight"])}
                                   for x in c["constituents"]]
        except (KeyError, TypeError, ValueError):
            f.missing.append({"url": FAPI + "constituents?symbol=" + sym, "error": "返回结构异常", "note": "constituents"})
    fh = f.get(FAPI + "fundingRate?symbol=%s&limit=%d" % (sym, funding_n), "fundingRate")
    if fh is not None:
        fs = funding_stats(fh)
        if fs is None:
            f.missing.append({"url": FAPI + "fundingRate?symbol=" + sym, "error": "返回空列表", "note": "fundingRate"})
        else:
            out["funding"] = fs
    if onboard_ms:  # 上线后最早的几条资金费记录：结算周期是否一直没变（用来核对新闻与接口的冲突）
        fe = f.get(FAPI + "fundingRate?symbol=%s&startTime=%d&limit=3" % (sym, int(onboard_ms)), "fundingRate earliest")
        if fe and len(fe) >= 2:
            ts = [x["fundingTime"] for x in fe]
            out["funding_earliest"] = {"times_utc": [dt.datetime.fromtimestamp(t / 1000, UTC).strftime("%Y-%m-%d %H:%M") for t in ts],
                                       "interval_hours": round((ts[1] - ts[0]) / 3.6e6, 2)}
    k = f.get(FAPI + "klines?symbol=%s&interval=1h&limit=%d" % (sym, kline_n), "klines")
    if k is not None:
        if k:
            out["klines_1h"] = [_row(x) for x in k]
        else:
            f.missing.append({"url": FAPI + "klines?symbol=" + sym, "error": "返回空列表", "note": "klines"})
    return out


def main():
    ap = argparse.ArgumentParser(description="币安 TradFi 取数 -> snapshot.json")
    ap.add_argument("--category", required=True, choices=["survey", "commodity", "fx", "index", "equity", "custom"])
    ap.add_argument("--symbols", help="逗号分隔，覆盖自动选取（custom 必填）")
    ap.add_argument("--pairs", help="现货:永续 对照，如 PAXGUSDT:XAUUSDT,USDTBRL:USDBRLUSDT")
    ap.add_argument("--auto-bstocks", action="store_true", help="按名称规则自动配对 bStocks（base+B）")
    ap.add_argument("--watchlist", default=os.path.join(os.path.dirname(__file__), "..", "config", "index_watchlist.json"))
    ap.add_argument("--funding-n", type=int, default=60)
    ap.add_argument("--kline-n", type=int, default=500)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    f = Fetcher()
    ex = check_connectivity(f)
    now = dt.datetime.now(UTC)
    now_et = now.astimezone(ET)
    snap = {"meta": {"tool_version": VERSION, "fetched_at_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                     "fetched_weekday_utc": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
                     "fetched_at_et": now_et.strftime("%Y-%m-%d %H:%M") + " " + now_et.tzname(),
                     "category": a.category}}

    # 合约清单
    tradfi = [s for s in ex["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL"]
    cnt = {}
    for s in tradfi:
        cnt[s.get("underlyingType", "?")] = cnt.get(s.get("underlyingType", "?"), 0) + 1
    snap["inventory"] = {
        "tradfi_total": len(tradfi), "by_underlying_type": cnt,
        "status_counts": {k: sum(1 for s in tradfi if s.get("status") == k) for k in {s.get("status") for s in tradfi}},
        "index_name_scan": [{"symbol": s["symbol"], "underlyingType": s.get("underlyingType")}
                            for s in ex["symbols"] if INDEX_NAME_PATTERN.search(s["symbol"])],
    }
    info = {s["symbol"]: s for s in ex["symbols"]}
    snap["survey"], spot_info, eapi_info = build_survey(f, ex)

    # 选标的
    watch_labels = {}
    if a.category == "index":
        wl = json.load(open(a.watchlist, encoding="utf-8"))
        watch_labels = {x["symbol"]: x for x in wl["contracts"]}
        snap["inventory"]["watchlist_note"] = wl.get("note", "")
    if a.symbols:
        syms = [x.strip() for x in a.symbols.split(",") if x.strip()]
    elif a.category == "commodity":
        syms = [s["symbol"] for s in tradfi if s.get("underlyingType") == "COMMODITY"]
    elif a.category == "fx":
        syms = [s["symbol"] for s in tradfi if s.get("underlyingType") == "FX"]
    elif a.category == "equity":
        syms = [s["symbol"] for s in tradfi if s.get("underlyingType") == "EQUITY"]
    elif a.category == "index":
        syms = [x for x in watch_labels if x in info]
        snap["inventory"]["watchlist_not_found"] = [x for x in watch_labels if x not in info]
    elif a.category == "survey":
        syms = []
    else:
        sys.exit("custom 必须提供 --symbols")
    for x in syms:
        if x not in info:  # 不在永续 exchangeInfo 里：必须留痕，不能静默丢掉
            f.missing.append({"url": FAPI + "exchangeInfo", "error": "合约 %s 不存在" % x, "note": "symbol not found"})
            print("警告：合约 %s 不在币安 U 本位永续清单里，已跳过" % x, file=sys.stderr)
    syms = [s for s in syms if s in info]
    snap["symbols"] = {}
    fundinginfo = {x["symbol"]: x for x in (f.get(FAPI + "fundingInfo", "fundingInfo") or [])}
    for s in syms:
        d = fetch_symbol(f, s, a.funding_n, a.kline_n, info[s].get("onboardDate"))
        i = info[s]
        d["underlying_type"] = i.get("underlyingType")
        d["contract_type"] = i.get("contractType")
        d["status"] = i.get("status")
        d["margin_asset"] = i.get("marginAsset")
        if i.get("onboardDate"):
            d["onboard_date"] = dt.datetime.fromtimestamp(i["onboardDate"] / 1000, UTC).strftime("%Y-%m-%d")
        flt = {x["filterType"]: x for x in i.get("filters", [])}
        d["filters"] = {"tick": flt.get("PRICE_FILTER", {}).get("tickSize"), "min_qty": flt.get("LOT_SIZE", {}).get("minQty"),
                        "min_notional": flt.get("MIN_NOTIONAL", {}).get("notional"),
                        "percent_price_up": flt.get("PERCENT_PRICE", {}).get("multiplierUp"),
                        "liquidation_fee": i.get("liquidationFee"), "market_take_bound": i.get("marketTakeBound")}
        x = fundinginfo.get(s)
        if x:
            cap, flo = fnum(x.get("adjustedFundingRateCap")), fnum(x.get("adjustedFundingRateFloor"))
            if x.get("fundingIntervalHours") is not None and cap is not None and flo is not None:
                d["funding_info"] = {"interval_hours": x["fundingIntervalHours"], "cap_pct": cap * 100, "floor_pct": flo * 100}
        if s in watch_labels:
            d["label"] = watch_labels[s]["label"]
            d["leverage_note"] = watch_labels[s].get("multiple")
            d["identify_basis"] = watch_labels[s].get("basis", "")
        # 周末、时段统计
        rows = d.get("klines_1h")
        if rows and i.get("contractType") != "TRADIFI_PERPETUAL":
            # 加密币永续 24/7 交易：按美股/商品休市窗口算「周末」没有意义，不输出
            d["weekend_skipped_reason"] = "非 TRADIFI 永续（contractType=%s），休市窗口/美股时段统计不适用" % i.get("contractType")
        elif rows:
            kind = weekend_kind_for(d["underlying_type"])
            w0, w1 = weekend_window(now, kind)
            d["weekend"] = weekend_stats(rows, w0, w1)
            d["weekend_kind"] = kind
            d["sessions_et"] = session_stats(rows)
        snap["symbols"][s] = d

    # 现货对照
    pairs = []
    if a.pairs:
        for x in a.pairs.split(","):
            if x.count(":") != 1 or not all(x.split(":")):
                sys.exit("--pairs 格式应为 现货:永续，逗号分隔；收到 %r" % x)
            pairs.append(tuple(x.split(":")))
    if a.auto_bstocks:
        if spot_info:
            sset = {s["symbol"] for s in spot_info["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"}
            for s in syms:
                if info[s].get("contractType") != "TRADIFI_PERPETUAL":
                    continue  # 只给 TradFi 永续配 bStocks，避免 BTC→BTCB（币安锚定比特币）这类误配
                base = info[s]["baseAsset"]
                if base + "BUSDT" in sset:
                    pairs.append((base + "BUSDT", s))
    snap["spot_pairs"] = {}
    for sp, pf in pairs:
        if pf not in snap["symbols"]:
            f.missing.append({"url": SPOT + "ticker/24hr?symbol=" + sp, "error": "对照永续 %s 不在本次取数的 symbols 里，现货对照跳过" % pf,
                              "note": "pair skipped"})
            continue
        t = f.get(SPOT + "ticker/24hr?symbol=" + sp, "spot ticker")
        if not t:
            continue
        item = {"perp": pf, "quote_volume_24h": fnum(t.get("quoteVolume")), "last_price": fnum(t.get("lastPrice")),
                "price_change_pct_24h": fnum(t.get("priceChangePercent")),
                "auto_matched_by_name": bool(a.auto_bstocks and sp == info[pf]["baseAsset"] + "BUSDT" and not a.pairs)}
        d = f.get(SPOT + "depth?symbol=%s&limit=1000" % sp, "spot depth")
        if d and d.get("bids") and d.get("asks"):
            item["spread_bp"], item["depth_usd"] = usd_depth_bands(d["bids"], d["asks"])
        k = f.get(SPOT + "klines?symbol=%s&interval=1h&limit=168" % sp, "spot klines")
        prows = snap["symbols"][pf].get("klines_1h")
        if k and prows:
            wk = None
            w = snap["symbols"][pf].get("weekend")
            if w:
                wk = (dt.datetime.strptime(w["window_utc"][0], "%Y-%m-%d %H:%M").replace(tzinfo=UTC),
                      dt.datetime.strptime(w["window_utc"][1], "%Y-%m-%d %H:%M").replace(tzinfo=UTC))
            item["basis_7d"] = basis_stats([_row(x) for x in k], prows, wk)
        snap["spot_pairs"][sp] = item

    # 类别附加
    if a.category == "commodity":
        o = eapi_info
        if o:
            unds = {}
            for s in o["optionSymbols"]:
                u = s["underlying"]
                e = unds.setdefault(u, {"count": 0, "expiries_utc": set(), "strikes": set(), "types": set()})
                e["count"] += 1
                e["expiries_utc"].add(dt.datetime.fromtimestamp(s["expiryDate"] / 1000, UTC).strftime("%m-%d %H:%M"))
                e["strikes"].add(float(s["strikePrice"]))
                e["types"].add(s.get("contractType", ""))
            snap["options"] = {
                "contracts": [{"underlying": c["underlying"], "nakedSell": c["nakedSell"]} for c in o["optionContracts"]],
                "listed": {u: {"count": e["count"], "expiries_utc": sorted(e["expiries_utc"]),
                               "strike_min": min(e["strikes"]), "strike_max": max(e["strikes"]),
                               "n_strikes": len(e["strikes"]), "types": sorted(e["types"])} for u, e in unds.items()}}
    if a.category == "fx":
        si = spot_info
        if si:
            quoted, base = {}, {}
            for s in si["symbols"]:
                if s["status"] != "TRADING":
                    continue
                if s["quoteAsset"] in FIAT:
                    quoted[s["quoteAsset"]] = quoted.get(s["quoteAsset"], 0) + 1
                if s["baseAsset"] in FIAT:
                    base.setdefault(s["baseAsset"], []).append(s["symbol"])
            snap["fiat_spot"] = {"pairs_quoted_in_fiat": dict(sorted(quoted.items(), key=lambda x: -x[1])),
                                 "fiat_as_base": {k: v[:6] for k, v in base.items()}}
    if a.category == "index":
        si = spot_info
        if si:
            b = sorted({s["baseAsset"] for s in si["symbols"] if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
                        and s["baseAsset"].endswith("B") and s["baseAsset"][:-1] in {x["baseAsset"] for x in tradfi if "baseAsset" in x}})
            snap["bstocks_candidates"] = {"rule": "现货 USDT 交易对，base 以 B 结尾且去掉 B 后与某个 TradFi 永续合约的 baseAsset 相同",
                                          "count": len(b), "bases": b,
                                          "caveat": "按代号规则筛，可能有误判或漏判，不是官方清单"}

    snap["meta"]["endpoints_used"] = f.used
    snap["missing"] = f.missing
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1)
    print("OK %s | symbols=%d | missing=%d | %s" % (a.out, len(snap["symbols"]), len(f.missing), snap["meta"]["fetched_at_utc"]))
    if f.missing:
        print("以下请求失败，对应字段写作「数据缺失」：", file=sys.stderr)
        for m in f.missing[:10]:
            print("  ", m["error"], m["url"], file=sys.stderr)


if __name__ == "__main__":
    main()
