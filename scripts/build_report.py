#!/usr/bin/env python3
"""snapshot.json + narrative.html -> 报告 HTML。

数字全部来自 snapshot，不手抄。narrative 里用 <!--AUTO:xxx--> 占位，用 {V}{G}{U}{I} 打标签。
拿不到的数据一律渲染为「数据缺失」。
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as ledger_mod  # noqa: E402
TAGS = {
    "{V}": '<span class="tag v">实测</span>',
    "{G}": '<span class="tag g">转述</span>',
    "{U}": '<span class="tag u">未证实</span>',
    "{I}": '<span class="tag">推论</span>',
}
LEGEND = ('可信度标记：' + TAGS["{V}"] + 'API 直接拿到　' + TAGS["{G}"] + '官方公告/新闻稿/媒体，读到但没能对原文　'
          + TAGS["{U}"] + '找不到可靠来源，别当真　' + TAGS["{I}"] + '从数据推的，不是官方说法')
MISSING = '<span class="u">数据缺失</span>'


def esc(x):
    """接口/快照里来的任何字符串进 HTML 前必须转义。"""
    return html.escape(str(x))


def usd(x):
    if x is None:
        return MISSING
    if x >= 1e8:
        return "$%.2f亿" % (x / 1e8)
    if x >= 1e6:
        return "$%.0f万" % (x / 1e4)
    if x >= 1e4:
        return "$%.1f万" % (x / 1e4)
    return "$%.0f" % x


def num(x, nd=2, suffix=""):
    return MISSING if x is None else ("%." + str(nd) + "f%s") % (x, suffix)


def table(head, rows):
    h = "".join("<th>%s</th>" % x for x in head)
    r = "".join("<tr>" + "".join("<td>%s</td>" % c for c in row) + "</tr>" for row in rows)
    return '<div class="tw"><table><tr>%s</tr>%s</table></div>' % (h, r)


def wrap(title_tag, inner, note=""):
    n = '<p class="sub">%s</p>' % note if note else ""
    return '<div class="auto">%s%s%s</div>' % (n, inner, "")


class Ctx:
    def __init__(self, snaps, led=None, led_path=""):
        self.snaps = snaps
        self.ledger, self.ledger_path = led, led_path
        self.used_sources, self.cite_errors = [], []
        self.symbols = {}
        self.spot_pairs = {}
        for s in snaps:
            for key, dst in (("symbols", self.symbols), ("spot_pairs", self.spot_pairs)):
                for k, v in (s.get(key) or {}).items():
                    if k in dst and dst[k] != v:
                        print("警告：多个快照都含 %s %s 且内容不同，后面的覆盖前面的" % (key, k), file=sys.stderr)
                    dst[k] = v

    def first(self, key):
        for s in self.snaps:
            if key in s:
                return s[key]
        return None


def sel(ctx, arg):
    if arg and arg != "all":
        want = arg.split("+")
        for x in want:
            if x not in ctx.symbols:
                print("警告：占位符里指定的 %s 不在快照里，已忽略" % x, file=sys.stderr)
        return [x for x in want if x in ctx.symbols]
    return list(ctx.symbols)


def g_meta(ctx, arg):
    metas = [s.get("meta", {}) for s in ctx.snaps]
    times = []
    for m in metas:
        t = esc(m.get("fetched_at_utc", "未知"))
        if m.get("fetched_weekday_utc"):
            t += "（%s；美东 %s）" % (esc(m["fetched_weekday_utc"]), esc(m.get("fetched_at_et", "")))
        times.append(t)
    eps = sorted({e for s in ctx.snaps for e in s.get("meta", {}).get("endpoints_used", [])})
    return ('<p class="sub">数据快照：%s UTC。标「实测」的数字都是这个时间点直接打币安公开 API 拿的，会变。'
            '24h 成交等统计是取数前的滚动 24 小时，不是日历日，所以取数在周末时窗口仍会包含周五的交易。<br>%s</p>'
            '<details class="sub"><summary>本次用到的接口（%d 个）</summary><div>%s</div></details>'
            % (" / ".join(times), LEGEND, len(eps), "<br>".join("<code>%s</code>" % esc(e) for e in eps)))


def g_inventory(ctx, arg):
    inv = ctx.first("inventory")
    if not inv:
        return MISSING
    rows = [[esc(k), esc(v)] for k, v in sorted(inv["by_underlying_type"].items(), key=lambda x: -x[1])]
    out = "<p>币安 TradFi 永续共 <b>%d</b> 个 %s</p>" % (inv["tradfi_total"], TAGS["{V}"])
    out += table(["标的类型 underlyingType", "合约数"], rows)
    hits = inv.get("index_name_scan", [])
    out += "<p class=\"sub\">按名称扫描全部永续合约（NAS/SPX/US500/NDX/DJI/DAX/HSI/N225/VIX/DXY 等）：命中 %s</p>" % (
        "、".join("%s（%s）" % (esc(h["symbol"]), esc(h.get("underlyingType", "?"))) for h in hits) or "无")
    if inv.get("watchlist_not_found"):
        out += "<p class=\"sub\">观察清单里当前不存在的合约：%s</p>" % "、".join(esc(x) for x in inv["watchlist_not_found"])
    return out


def g_contracts(ctx, arg):
    rows = []
    for s in sel(ctx, arg):
        d = ctx.symbols[s]
        dep = d.get("depth_usd", {}).get("0.5")
        rows.append([
            "<b>%s</b>%s" % (esc(s), ("<br><span class=\"sub\">%s</span> %s" % (esc(d["label"]), TAGS["{I}"])) if d.get("label") else ""),
            usd(d.get("quote_volume_24h")), usd(d.get("open_interest_usd")),
            num(d.get("price_change_pct_24h"), 2, "%"), num(d.get("spread_bp"), 2, "bp"),
            "%s / %s" % (usd(dep[0]), usd(dep[1])) if dep else MISSING,
            num(d.get("mark_vs_index_bp"), 1, "bp"), esc(d["onboard_date"]) if d.get("onboard_date") else MISSING])
    return table(["合约", "24h 成交", "未平仓", "24h 涨跌", "买一卖一价差", "±0.5% 深度 买/卖", "标记价−指数价", "上线日"], rows)


def g_constituents(ctx, arg):
    rows = []
    for s in sel(ctx, arg):
        c = ctx.symbols[s].get("constituents")
        if not c:
            rows.append([esc(s), MISSING])
            continue
        c = sorted(c, key=lambda x: -x["weight"])
        rows.append([esc(s), "；".join("%s <code>%s</code> %.2f%%" % (esc(x["exchange"]), esc(x["symbol"]), x["weight"] * 100)
                                for x in c)])
    return table(["合约", "指数成分（交易所/数据商 代号 权重）"], rows)


def g_funding(ctx, arg):
    rows = []
    for s in sel(ctx, arg):
        d = ctx.symbols[s]
        fi, f = d.get("funding_info"), d.get("funding")
        if not f:
            rows.append([esc(s)] + [MISSING] * 7)
            continue
        rows.append([
            esc(s), ("%sh" % esc(fi["interval_hours"])) if fi else MISSING,
            ("±%.3f%%" % fi["cap_pct"]) if fi else MISSING,
            num(d.get("last_funding_rate_pct"), 4, "%"),
            "%d 期中 %d 期为 0（周末 %d 期）" % (f["n"], f["zero_n"], f["weekend_zero_n"]),
            num(f["nonzero_mean_pct"], 4, "%"), "%.4f%% ~ %.4f%%" % (f["min_pct"], f["max_pct"]), "%.3f%%" % f["sum_pct"]])
    return table(["合约", "结算周期", "上下限", "最近一期", "统计区间", "非零期平均", "范围", "累计"], rows)


def g_weekend(ctx, arg):
    rows = []
    win = ""
    for s in sel(ctx, arg):
        w = ctx.symbols[s].get("weekend")
        if not w:
            why = ctx.symbols[s].get("weekend_skipped_reason")
            if why:  # 脚本主动跳过（例如非 TradFi 永续）：写明原因，不要让作者去猜「数据缺失」的原因
                rows.append([esc(s), '<span class="sub">不适用：%s</span>' % esc(why), "", "", "", ""])
            else:
                rows.append([esc(s)] + [MISSING] * 5)
            continue
        win = "%s ~ %s UTC，平日=数据内完整的 UTC 周一到周四（%d 天）" % (esc(w["window_utc"][0]), esc(w["window_utc"][1]), w["weekday_days"])
        rows.append([esc(s), "%.1f%%" % w["vol_ratio_pct"], "%.2f%%" % w["weekend_range_pct"],
                     "%.2f%%" % w["weekday_avg_daily_range_pct"],
                     num(w["reopen_gap_pct"], 2, "%"), usd(w["weekend_vol_per_h"]) + " / " + usd(w["weekday_vol_per_h"])])
    if not rows:
        return MISSING
    if not win:
        win = "无可计算的窗口"
    return '<p class="sub">休市窗口：%s。%s</p>' % (win, TAGS["{V}"]) + table(
        ["合约", "周末每小时成交 ÷ 平日", "周末高低差", "平日单日平均高低差", "窗口前收盘到窗口后开盘跳空", "周末/平日 每小时成交额"], rows)


def g_sessions(ctx, arg):
    rows = []
    for s in sel(ctx, arg):
        ss = ctx.symbols[s].get("sessions_et")
        if not ss:
            continue
        for k, v in ss.items():
            rows.append([esc(s), esc(k), str(v["n_hours"]), usd(v["vol_per_h"]), "%.3f%%" % v["range_pct"], str(v["trades_per_h"])])
    if not rows:
        return MISSING
    return '<p class="sub">按美东时间分桶，仅 ET 周一到周五，最后一根未走完的 K 线已丢弃。%s</p>' % TAGS["{V}"] + table(
        ["合约", "时段", "样本小时数", "每小时成交额", "每小时平均振幅", "每小时成交笔数"], rows)


def g_basis(ctx, arg):
    rows = []
    for sp, d in ctx.spot_pairs.items():
        if arg and arg != "all" and sp not in arg.split("+"):
            continue
        b = d.get("basis_7d") or {}
        dep = d.get("depth_usd", {}).get("0.5")
        pd_ = (ctx.symbols.get(d.get("perp")) or {})
        pdep = pd_.get("depth_usd", {}).get("0.5")
        rows.append([esc(sp) + (" <span class=\"sub\">(按名称自动配对)</span>" if d.get("auto_matched_by_name") else ""), esc(d.get("perp", "?")),
                     usd(d.get("quote_volume_24h")) + " / " + usd(pd_.get("quote_volume_24h")), num(d.get("spread_bp"), 2, "bp"),
                     ("%s / %s" % (usd(dep[0]), usd(dep[1])) if dep else MISSING) + " ｜ " + ("%s / %s" % (usd(pdep[0]), usd(pdep[1])) if pdep else MISSING),
                     num(b.get("mean_bp"), 1, "bp"),
                     ("%.1f ~ %.1fbp" % (b["min_bp"], b["max_bp"])) if b else MISSING,
                     num(b.get("weekend_mean_bp"), 1, "bp")])
    if not rows:
        return MISSING
    return '<p class="sub">价差＝现货收盘价相对永续收盘价（负数表示现货更便宜），7 天 1 小时 K 线。%s</p>' % TAGS["{V}"] + table(
        ["现货", "对照永续", "24h 成交（现货 / 永续）", "现货买一卖一价差", "±0.5% 深度 买/卖（现货 ｜ 永续）", "7 天平均价差", "7 天范围", "周末平均价差"], rows)


def g_options(ctx, arg):
    o = ctx.first("options")
    if not o:
        return MISSING
    rows = []
    for u, e in o["listed"].items():
        rows.append([esc(u), str(e["count"]), "、".join(esc(x) for x in e["expiries_utc"]),
                     "%g ~ %g（%d 档）" % (e["strike_min"], e["strike_max"], e["n_strikes"]), "、".join(esc(x) for x in e["types"])])
    out = table(["标的", "挂牌合约数", "到期日（UTC）", "行权价范围", "合约类型"], rows)
    out += "<p class=\"sub\">期权标的清单与是否允许裸卖（nakedSell）：%s %s</p>" % (
        "；".join("%s：%s" % (esc(c["underlying"]), "允许" if c["nakedSell"] else "不允许") for c in o["contracts"]), TAGS["{V}"])
    return out


def g_fiat(ctx, arg):
    fs = ctx.first("fiat_spot")
    if not fs:
        return MISSING
    out = table(["计价法币", "现货交易对数量"], [[esc(k), esc(v)] for k, v in fs["pairs_quoted_in_fiat"].items()])
    out += "<p class=\"sub\">法币作为 base 的交易对（部分）：%s %s</p>" % (
        "；".join("%s：%s" % (esc(k), "、".join(esc(x) for x in v)) for k, v in fs["fiat_as_base"].items()) or "无", TAGS["{V}"])
    return out


def g_bstocks(ctx, arg):
    b = ctx.first("bstocks_candidates")
    if not b:
        return MISSING
    return '<p>%d 个候选：%s</p><p class="sub">规则：%s。%s</p>' % (
        b["count"], "、".join(esc(x) for x in b["bases"]), esc(b["rule"]), esc(b["caveat"]))


def g_scope(ctx, arg):
    sv = ctx.first("survey")
    if not sv:
        return MISSING
    rows = [[html.escape(e["scanned"]), "<code>%s</code>" % html.escape(e["endpoint"].replace("https://", "")),
             str(e["count"]) if e.get("count") is not None else MISSING] for e in sv["scope"]]
    out = '<p>本报告里所有「没有 X」的结论，范围<b>仅限</b>下面实际扫过的接口 %s</p>' % TAGS["{V}"]
    out += table(["扫了什么", "接口", "数量"], rows)
    out += "<p><b>没扫的：</b>%s。这些地方有没有，本报告不下结论。</p>" % "；".join(html.escape(x) for x in sv["not_scanned"])
    return out


def g_survey(ctx, arg):
    sv = ctx.first("survey")
    if not sv:
        return MISSING
    f, out = sv["fapi"], ""
    kv = lambda d: [[html.escape(str(k)), str(v)] for k, v in d.items()]
    out += "<h3>U 本位合约（共 %d 个）</h3>" % f["total"] + table(["合约类型 contractType", "数量"], kv(f["by_contract_type"]))
    out += "<p>状态分布：%s；其中 SETTLING 共 %d 个，类型分布 %s</p>" % (
        "、".join("%s %d" % kv_ for kv_ in f["by_status"].items()), f["settling_total"],
        "、".join("%s %d" % kv_ for kv_ in f["settling_by_contract_type"].items()) or "无")
    out += "<h3>TradFi 永续（共 %d 个）</h3>" % f["tradfi_total"] + table(["标的类型 underlyingType", "数量"], kv(f["tradfi_by_underlying_type"]))
    out += "<p>保证金币种：%s；状态：%s</p>" % ("、".join("%s %d 个" % kv_ for kv_ in f["tradfi_margin_assets"].items()),
                                       "、".join("%s %d" % kv_ for kv_ in f["tradfi_by_status"].items()))
    out += "<h3>标的类型为 INDEX 的合约（%d 个）</h3>" % len(f["index_contracts"]) + table(
        ["合约", "状态", "合约类型", "子类型"], [[x["symbol"], x["status"], x["contractType"], html.escape("、".join(x["subtypes"]))] for x in f["index_contracts"]])
    out += "<h3>U 本位交割（季度）合约（%d 个）</h3>" % len(f["delivery_contracts"]) + table(
        ["合约", "类型", "标的类型"], [[x["symbol"], x["contractType"], x["underlyingType"]] for x in f["delivery_contracts"]])
    if sv.get("dapi"):
        d = sv["dapi"]
        out += "<h3>币本位合约（共 %d 个）</h3>" % d["total"] + table(["合约类型", "数量"], kv(d["by_contract_type"]))
        out += "<p>标的类型：%s</p>" % "、".join("%s %d" % kv_ for kv_ in d["by_underlying_type"].items())
    if sv.get("spot"):
        out += "<p>现货交易对（TRADING）共 %d 个。</p>" % sv["spot"]["trading_symbols"]
    if sv.get("eapi"):
        lc = sv["eapi"].get("listed_count", {})
        out += "<h3>币安自己的期权：标的与当前挂牌合约数</h3>" + table(
            ["标的", "当前挂牌合约数", "是否允许裸卖"],
            [[html.escape(u), str(lc.get(u, 0)) if u in lc else "0（标的清单里有，但当前没有挂牌合约）",
              "允许" if sv["eapi"].get("naked_sell", {}).get(u) else "不允许"] for u in sv["eapi"]["underlyings"]])
    names = ["U 本位：" + ("、".join("%s（%s）" % (x["symbol"], x["underlyingType"]) for x in f["name_scan"]) or "无")]
    if sv.get("dapi"):
        names.append("币本位：" + ("、".join(x["symbol"] for x in sv["dapi"]["name_scan"]) or "无"))
    if sv.get("spot"):
        names.append("现货：" + ("、".join(x["symbol"] for x in sv["spot"]["name_scan"]) or "无"))
    out += '<p class="sub">名称扫描（NAS/SPX/US500/NDX/DJI/DAX/HSI/N225/VIX/DXY 等）命中：%s</p>' % html.escape("；".join(names))
    return out


def g_volume(ctx, arg):
    sv = ctx.first("survey")
    v = (sv or {}).get("fapi", {}).get("tradfi_volume_24h")
    if not v:
        return MISSING
    out = '<p>TradFi 永续 24h 成交额合计 %s（%d 个合约）。%s</p>' % (usd(v["total_usd"]), v["n"], TAGS["{V}"])
    out += table(["24h 成交额区间", "合约数"], [[k, str(n)] for k, n in v["buckets"].items()])
    out += table(["成交额前十", "24h 成交额"], [[x["symbol"], usd(x["usd"])] for x in v["top10"]])
    return out


# ---------- 来源台账引用：{G:id|原文片段+id2|片段2}  {U:id} ----------

class CiteError(Exception):
    pass


def render_cites(text, led, led_path, used):
    """把 {G:..}/{U:..} 渲染成带来源编号的标签；核对来源与原文片段。失败收集到 errors。"""
    errors = []

    def rep(m):
        tag, body = m.group(1), m.group(2)
        cites = []
        for part in body.split("+"):
            sid, _, quote = part.partition("|")
            sid, quote = sid.strip(), quote.strip()
            ok, why = ledger_mod.verify_cite(led, led_path, sid, quote, tag) if led is not None else (False, "未提供来源台账（--sources）")
            if not ok:
                errors.append("{%s:%s} → %s" % (tag, part, why))
            if sid not in used:
                used.append(sid)
            cites.append((sid, quote))
        nums = "".join('<sup><a href="#src-%s">[%d]</a></sup>' % (html.escape(sid), used.index(sid) + 1) for sid, _ in cites)
        covers = "；".join(html.escape((led or {}).get("sources", {}).get(sid, {}).get("covers", "")) for sid, _ in cites)
        cls = "tag g" if tag == "G" else "tag u"
        label = "转述" if tag == "G" else "未证实"
        return '<span class="%s" data-cite="%s" data-covers="%s">%s%s</span>' % (
            cls, html.escape(body, quote=True), covers, label, nums)
    out = re.sub(r"\{([GU]):([^}]+)\}", rep, text)
    return out, errors


def g_sources(ctx, arg):
    led = getattr(ctx, "ledger", None)
    used = getattr(ctx, "used_sources", [])
    if not led:
        return MISSING
    rows = []
    for i, sid in enumerate(used, 1):
        e = led.get("sources", {}).get(sid)
        if not e:
            rows.append(['<a id="src-%s"></a>[%d]' % (esc(sid), i), esc(sid), MISSING, "", "", ""])
            continue
        rows.append(['<a id="src-%s"></a>[%d]' % (esc(sid), i), '<a href="%s">%s</a>' % (esc(e["url"]), esc(e.get("title") or e["url"])),
                     esc(e.get("status", "")), esc(e.get("fetched_at_utc", "")), esc(e.get("covers", "")), str(e.get("text_chars", ""))])
    return table(["编号", "来源", "抓取状态", "抓取时间(UTC)", "这个来源讲的范围", "原文字数"], rows) + \
        '<p class="sub">抓取状态 ok＝脚本直接抓到原文，转述句的片段已逐条核对；其他状态的来源只能支撑「未证实」。</p>'


def g_tradfilist(ctx, arg):
    sv = ctx.first("survey")
    lst = ((sv or {}).get("fapi") or {}).get("tradfi_symbols")
    if not lst:
        return MISSING
    want = [x for x in arg.split("+") if x] or list(lst)
    rows = []
    for t in want:
        for x in lst.get(t, []):
            rows.append([esc(t), esc(x["symbol"]), esc("、".join(x.get("subtypes", []))), esc(x.get("onboard", ""))])
    return table(["标的类型", "合约", "子类型", "上线日"], rows) if rows else MISSING


def g_missing(ctx, arg):
    miss = [m for s in ctx.snaps for m in s.get("missing", [])]
    if not miss:
        return '<p class="sub">本次取数没有失败的请求。</p>'
    return table(["失败请求", "错误"], [["<code>%s</code>" % esc(m.get("url", "?")), esc(m.get("error", "?"))] for m in miss])


GEN = {"meta": g_meta, "inventory": g_inventory, "contracts": g_contracts, "constituents": g_constituents,
       "funding": g_funding, "weekend": g_weekend, "sessions": g_sessions, "basis": g_basis, "options": g_options,
       "fiatpairs": g_fiat, "bstocks": g_bstocks, "missing": g_missing,
       "scope": g_scope, "survey": g_survey, "volume": g_volume,
       "sources": g_sources, "tradfilist": g_tradfilist}


# ---------- 叙述里的内联取数：{{v:SYM.path|fmt}} 取合约字段，{{i:path|fmt}} 取盘点字段 ----------

def _walk(obj, path):
    toks = re.findall(r"\[[^\]]*\]|[^.\[\]]+", path)
    for t in toks:
        if obj is None:
            return None
        if t.startswith("["):
            t = t[1:-1]
        if isinstance(obj, dict):
            if t not in obj:
                return None
            obj = obj[t]
        elif isinstance(obj, list):
            try:
                obj = obj[int(t)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return obj


def _fmt(val, fmt):
    if val is None:
        return None
    try:
        if fmt == "usd":
            return usd(float(val))
        if fmt == "pct":      # 小数 -> 百分数（权重）
            return "%.2f%%" % (float(val) * 100)
        if fmt == "pctn":     # 已经是百分数
            return "%.2f%%" % float(val)
        if fmt == "bp":
            return "%.1fbp" % float(val)
        if fmt == "len":
            return str(len(val))
        if fmt == "int":
            return "{:,}".format(int(round(float(val))))
        if fmt in ("f1", "f2", "f3"):
            return ("%." + fmt[1] + "f") % float(val)
    except (TypeError, ValueError):
        return None
    return html.escape(str(val)) if not isinstance(val, float) else "%.4g" % val


def resolve_inline(ctx, kind, path):
    if kind == "i":
        root = {}
        for sn in ctx.snaps:
            for k in ("survey", "inventory", "meta"):
                if k in sn and k not in root:
                    root[k] = sn[k]
        return _walk(root, path)
    sym, _, rest = path.partition(".")
    d = ctx.spot_pairs.get(sym) if kind == "s" else ctx.symbols.get(sym)
    if d is None:
        return None
    m = re.match(r"weight\[([^\]]+)\]$", rest)
    if m:
        key = m.group(1).lower()
        cs = d.get("constituents")
        if not cs:
            return None
        return sum(c["weight"] for c in cs if key in c["exchange"].lower())
    return _walk(d, rest)


def render_inline(text, ctx):
    def rep(m):
        kind, path, fmt = m.group(1), m.group(2).strip(), (m.group(3) or "raw")
        out = _fmt(resolve_inline(ctx, kind, path), fmt)
        if out is None:
            print("警告：内联占位 {{%s:%s}} 取不到值，输出「数据缺失」" % (kind, path), file=sys.stderr)
            return MISSING
        return '<span class="val" data-src="%s:%s">%s</span>' % (kind, html.escape(path), out)
    return re.sub(r"\{\{([vis]):([^}|]+)(?:\|([a-z0-9]+))?\}\}", rep, text)


def render(narr, ctx):
    def rep(m):
        parts = m.group(1).split(":", 1)
        name, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        if name not in GEN:
            sys.exit("未知占位符 AUTO:%s，可用：%s" % (name, ", ".join(GEN)))
        try:
            return "<!--AUTO-BEGIN-->" + GEN[name](ctx, arg) + "<!--AUTO-END-->"
        except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as e:
            # 快照缺字段/结构不对：该块整体写「数据缺失」，不崩、不编造
            print("警告：AUTO:%s 渲染失败（%s: %s），该块输出「数据缺失」" % (name, type(e).__name__, e), file=sys.stderr)
            return "<!--AUTO-BEGIN-->" + MISSING + "<!--AUTO-END-->"
    narr = re.sub(r"<!--GUIDE.*?-->", "", narr, flags=re.S)
    narr, cite_errors = render_cites(narr, getattr(ctx, "ledger", None), getattr(ctx, "ledger_path", ""), ctx.used_sources)
    ctx.cite_errors = cite_errors
    out = re.sub(r"<!--AUTO:([^>]*?)-->", rep, narr)
    out = render_inline(out, ctx)
    for k, v in TAGS.items():
        out = out.replace(k, v)
    return out


def build_nav(body):
    items = re.findall(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', body)
    return "<nav>" + "".join('<a href="#%s">%s</a>' % (i, re.sub(r"<[^>]+>", "", t)) for i, t in items) + "</nav>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="append", required=True)
    ap.add_argument("--narrative", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sources", help="来源台账 sources.json（默认取叙述稿同目录的 sources.json，存在才用）")
    ap.add_argument("--siblings", help="文件名:显示名,逗号分隔（用于「本系列」链接）")
    ap.add_argument("--current", help="当前文件名（在 siblings 里加粗）")
    a = ap.parse_args()

    snaps, h = [], hashlib.sha256()
    for p in a.snapshot:
        raw = open(p, "rb").read()
        h.update(raw)
        snaps.append(json.loads(raw))
    led_path = a.sources or os.path.join(os.path.dirname(os.path.abspath(a.narrative)), "sources.json")
    led = ledger_mod.load(led_path) if os.path.exists(led_path) else None
    led_sha = hashlib.sha256(open(led_path, "rb").read()).hexdigest() if led is not None else ""
    ctx = Ctx(snaps, led, led_path)
    narr = open(a.narrative, encoding="utf-8").read()
    body = render(narr, ctx)
    if ctx.cite_errors:
        print("来源引用核对失败（%d 处），不生成报告：" % len(ctx.cite_errors), file=sys.stderr)
        for e in ctx.cite_errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    left = re.findall(r"<!--AUTO:|\{[VGUI]\}|\{\{", body)
    if left:
        sys.exit("仍有未替换的占位符/标签：%s" % left[:5])
    nav = build_nav(body)
    body = body.replace("@@NAV@@", nav)
    sib = ""
    if a.siblings:
        parts = []
        for x in a.siblings.split(","):
            f, n = x.split(":", 1)
            parts.append("<b>%s</b>" % n if f == a.current else '<a href="%s">%s</a>' % (f, n))
        sib = '<div class="sib sub">本系列：%s</div>' % "　".join(parts)
    css = open(os.path.join(HERE, "..", "templates", "style.css"), encoding="utf-8").read()
    page = ('<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta name="snapshot-sha256" content="%s">\n<meta name="narrative-sha256" content="%s">\n<meta name="sources-sha256" content="%s">\n<meta name="snapshot-fetched-utc" content="%s">\n'
            '<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<main>\n%s\n%s\n</main>\n</body>\n</html>\n'
            % (h.hexdigest(), hashlib.sha256(narr.encode("utf-8")).hexdigest(), led_sha, " / ".join(esc(s.get("meta", {}).get("fetched_at_utc", "未知")) for s in snaps), html.escape(a.title), css, body, sib))
    open(a.out, "w", encoding="utf-8").write(page)
    print("OK", a.out, "snapshot sha256", h.hexdigest()[:12])


if __name__ == "__main__":
    main()
