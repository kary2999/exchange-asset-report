import datetime as dt
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import binance_data as bd  # noqa: E402

UTC = dt.timezone.utc


class TestDepth(unittest.TestCase):
    def test_bands_and_spread(self):
        bids = [["99.95", "10"], ["99.0", "5"], ["90", "100"]]
        asks = [["100.05", "10"], ["101", "5"], ["110", "100"]]
        spread, b = bd.usd_depth_bands(bids, asks, pcts=(0.1, 1.5))
        self.assertAlmostEqual(spread, 10.0, places=2)           # (100.05-99.95)/100 = 10bp
        self.assertEqual(b["0.1"], [round(99.95 * 10), round(100.05 * 10)])
        self.assertEqual(b["1.5"], [round(99.95 * 10 + 99.0 * 5), round(100.05 * 10 + 101 * 5)])


class TestFunding(unittest.TestCase):
    def test_stats(self):
        # 周六 00:00 UTC 为 0；周一 00:00 为 0.0001；周一 08:00 为 0.0003
        t = lambda d, h: int(dt.datetime(2026, 9, d, h, tzinfo=UTC).timestamp() * 1000)
        hist = [{"fundingTime": t(26, 0), "fundingRate": "0.00000000"},
                {"fundingTime": t(28, 0), "fundingRate": "0.00010000"},
                {"fundingTime": t(28, 8), "fundingRate": "0.00030000"}]
        s = bd.funding_stats(hist)
        self.assertEqual((s["n"], s["zero_n"], s["weekend_zero_n"]), (3, 1, 1))
        self.assertAlmostEqual(s["nonzero_mean_pct"], 0.02)
        self.assertAlmostEqual(s["sum_pct"], 0.04)


class TestWeekend(unittest.TestCase):
    def test_window_dst(self):
        # 夏令时：周五 17:00 EDT = 21:00 UTC，周日 18:00 EDT = 22:00 UTC
        now = dt.datetime(2026, 10, 3, 9, tzinfo=UTC)
        s, e = bd.weekend_window(now, "commodity")
        self.assertEqual((s.strftime("%m-%d %H"), e.strftime("%m-%d %H")), ("09-25 21", "09-27 22"))
        # 冬令时：周五 17:00 EST = 22:00 UTC
        s, e = bd.weekend_window(dt.datetime(2026, 12, 5, 9, tzinfo=UTC), "commodity")
        self.assertEqual((s.strftime("%m-%d %H"), e.strftime("%m-%d %H")), ("11-27 22", "11-29 23"))

    def test_window_not_yet_ended(self):
        # 周日 22:00 UTC 刚过 30 分钟：窗口刚结束不足 1 小时，应回退到上一个周末
        s, e = bd.weekend_window(dt.datetime(2026, 9, 27, 22, 30, tzinfo=UTC), "commodity")
        self.assertEqual(s.strftime("%m-%d"), "09-18")

    def _rows(self):
        rows = []
        start = dt.datetime(2026, 9, 21, 0, tzinfo=UTC)  # 周一
        for i in range(24 * 14):
            t = start + dt.timedelta(hours=i)
            wk = dt.datetime(2026, 9, 25, 21, tzinfo=UTC) <= t < dt.datetime(2026, 9, 27, 22, tzinfo=UTC)
            qv = 10.0 if wk else 100.0
            rows.append({"t": int(t.timestamp() * 1000), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "qv": qv, "n": 10})
        return rows

    def test_stats(self):
        s = dt.datetime(2026, 9, 25, 21, tzinfo=UTC)
        e = dt.datetime(2026, 9, 27, 22, tzinfo=UTC)
        w = bd.weekend_stats(self._rows(), s, e)
        self.assertEqual(w["weekend_hours"], 49)
        self.assertAlmostEqual(w["vol_ratio_pct"], 10.0)
        self.assertAlmostEqual(w["weekend_range_pct"], 2.0)
        self.assertAlmostEqual(w["reopen_gap_pct"], 0.0)

    def test_sessions_drop_last_partial(self):
        rows = self._rows()
        out = bd.session_stats(rows)
        total = sum(v["n_hours"] for v in out.values())
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        expected = sum(1 for r in rows[:-1] if dt.datetime.fromtimestamp(r["t"] / 1000, UTC).astimezone(et).weekday() < 5)
        self.assertEqual(total, expected)       # 最后一根被丢弃，美东周六周日被排除
        self.assertLess(total, len(rows))


class TestRetry(unittest.TestCase):
    def test_retry_then_ok_and_no_retry_on_4xx(self):
        import io
        import urllib.error
        import urllib.request
        calls = {"n": 0}

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def flaky(url, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionResetError("reset")
            return R(b'{"ok": 1}')

        def forbidden(url, timeout=0):
            calls["n"] += 1
            raise urllib.error.HTTPError(url, 451, "x", {}, None)

        orig, osleep = urllib.request.urlopen, bd.time.sleep
        bd.time.sleep = lambda s: None
        try:
            urllib.request.urlopen = flaky
            f = bd.Fetcher()
            self.assertEqual(f.get("https://x/y"), {"ok": 1})
            self.assertEqual(calls["n"], 3)
            calls["n"] = 0
            urllib.request.urlopen = forbidden
            f2 = bd.Fetcher()
            self.assertIsNone(f2.get("https://x/z"))
            self.assertEqual(calls["n"], 1)       # 451 不重试
            self.assertEqual(f2.missing[0]["error"], "HTTP 451")
        finally:
            urllib.request.urlopen, bd.time.sleep = orig, osleep


class TestLedger(unittest.TestCase):
    FX = os.path.join(os.path.dirname(__file__), "fixtures")

    def test_verify_cite(self):
        import ledger
        led = ledger.load(os.path.join(self.FX, "sources.json"))
        lp = os.path.join(self.FX, "sources.json")
        ok, _ = ledger.verify_cite(led, lp, "pr_gold_silver", "offered by  NEST exchange limited", "G")   # 大小写/空白不敏感
        self.assertTrue(ok)
        ok, why = ledger.verify_cite(led, lp, "pr_gold_silver", "Alpaca Securities", "G")
        self.assertFalse(ok)
        self.assertIn("找不到片段", why)
        ok, why = ledger.verify_cite(led, lp, "theblock", "anything", "G")
        self.assertFalse(ok)
        self.assertIn("只能写 {U}", why)
        ok, _ = ledger.verify_cite(led, lp, "theblock", "", "U")        # 抓不到原文的来源允许 {U}
        self.assertTrue(ok)
        ok, why = ledger.verify_cite(led, lp, "nope", "x", "U")
        self.assertFalse(ok)
        ok, why = ledger.verify_cite(led, lp, "pr_gold_silver", "", "G")  # 转述必须给片段
        self.assertFalse(ok)

    def test_add_source_status(self):
        import ledger
        import tempfile
        d = tempfile.mkdtemp()
        lp = os.path.join(d, "s.json")
        page = "<html><body><p>" + ("正文 text " * 200) + "</p><script>var x=1</script></body></html>"
        e = ledger.add_source(lp, "ok1", "https://x/a", fetcher=lambda u: (200, "text/html", page.encode(), ""))
        self.assertEqual(e["status"], "ok")
        self.assertNotIn("var x", ledger.get_text(lp, "ok1"))
        e = ledger.add_source(lp, "b1", "https://x/b", fetcher=lambda u: (403, "text/html", b"denied", "HTTP 403"))
        self.assertEqual(e["status"], "blocked")
        e = ledger.add_source(lp, "e1", "https://x/c", fetcher=lambda u: (200, "text/html", b"<html><body>loading</body></html>", ""))
        self.assertEqual(e["status"], "empty")
        e = ledger.add_source(lp, "c1", "https://x/d", fetcher=lambda u: (200, "text/html", b"<html><body>Just a moment... Cloudflare</body></html>", ""))
        self.assertEqual(e["status"], "blocked")
        e = ledger.add_source(lp, "n1", "https://x/e", fetcher=lambda u: (0, "", b"", "timeout"))
        self.assertEqual(e["status"], "error")
        with self.assertRaises(ValueError):
            ledger.add_source(lp, "bad id!", "https://x/f", fetcher=lambda u: (200, "", b"", ""))

    def _build(self, narr_text):
        import tempfile
        d = tempfile.mkdtemp()
        n = os.path.join(d, "n.html")
        open(n, "w", encoding="utf-8").write(narr_text)
        import shutil
        shutil.copy(os.path.join(self.FX, "sources.json"), os.path.join(d, "sources.json"))
        shutil.copytree(os.path.join(self.FX, "sources"), os.path.join(d, "sources"))
        out = os.path.join(d, "o.html")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot",
                            os.path.join(self.FX, "mini_snapshot.json"), "--narrative", n, "--title", "t", "--out", out],
                           capture_output=True, text=True)
        return r, out

    def test_build_rejects_bad_citations(self):
        r, out = self._build("<h2 id='s0'>x</h2><ul><li>新闻稿写了某事。{G:pr_gold_silver|这句话原文里没有}</li>"
                             "<li>被拦来源的转述。{G:theblock|abc}</li><li>未知来源。{U:ghost}</li></ul>")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(out))
        self.assertIn("找不到片段", r.stderr)
        self.assertIn("只能写 {U}", r.stderr)
        self.assertIn("不在台账里", r.stderr)

    def test_build_renders_citation_and_sources_table(self):
        r, out = self._build("<h2 id='s0'>x</h2><ul><li>黄金白银永续由 Nest Exchange Limited 提供。{G:pr_gold_silver|Nest Exchange Limited}</li></ul><!--AUTO:sources-->")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = open(out, encoding="utf-8").read()
        self.assertIn('href="#src-pr_gold_silver"', h)
        self.assertIn('id="src-pr_gold_silver"', h)
        self.assertIn("黄金白银永续首发", h)       # covers
        self.assertIn('name="sources-sha256"', h)

    def test_rules_g_nocite_generalize_explain(self):
        import claims
        html = ('<ul><li>币安股票永续都是 20 倍杠杆。<span class="tag g">转述</span></li>'
                '<li>币安股票永续都是 20 倍杠杆。<span class="tag g" data-cite="a|b" data-covers="只讲了 5 个合约">转述</span><span class="tag g" data-cite="a|b" data-covers="只讲了 5 个合约"></span></li>'
                '<li>周末成交偏低，因为周六休市。<span class="tag">推论</span></li>'
                '<li>周末成交偏低，可能因为周六休市，没验证。<span class="tag">推论</span></li></ul>')
        iss, _ = claims.analyze(html)
        rules = [(l, r) for l, r, _ in iss]
        self.assertIn(("FAIL", "G_NOCITE"), rules)
        self.assertIn(("WARN", "G_GENERALIZE"), rules)
        self.assertTrue(any("只讲了 5 个合约" in s for l, r, s in iss if r == "G_GENERALIZE"))
        self.assertEqual(sum(1 for l, r in rules if r == "I_EXPLAIN"), 1)   # 只有无限定的那句


class TestBasis(unittest.TestCase):
    def test_basis(self):
        spot = [{"t": 1, "c": 101.0}, {"t": 2, "c": 99.0}]
        perp = [{"t": 1, "c": 100.0}, {"t": 2, "c": 100.0}]
        b = bd.basis_stats(spot, perp)
        self.assertEqual((b["mean_bp"], b["min_bp"], b["max_bp"]), (0.0, -100.0, 100.0))


class TestBuildAndCheck(unittest.TestCase):
    def test_pipeline(self):
        fx = os.path.join(os.path.dirname(__file__), "fixtures")
        out = os.path.join(fx, "_out.html")
        py = sys.executable
        r = subprocess.run([py, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot", os.path.join(fx, "mini_snapshot.json"),
                            "--narrative", os.path.join(fx, "narrative_min.html"), "--title", "t", "--out", out],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        html = open(out, encoding="utf-8").read()
        self.assertIn("$1.50亿", html)       # 1.5e8 -> 亿
        self.assertIn("$526万", html)        # 5.26e6 -> 万
        self.assertIn("数据缺失", html)       # 缺字段不得被编造
        self.assertNotIn("<!--AUTO:", html)
        c = subprocess.run([py, os.path.join(ROOT, "scripts", "check_report.py"), out, "--snapshot",
                            os.path.join(fx, "mini_snapshot.json")], capture_output=True, text=True)
        self.assertEqual(c.returncode, 0, c.stdout)
        # 快照被改动后必须不合格
        tampered = os.path.join(fx, "_tampered.json")
        d = json.load(open(os.path.join(fx, "mini_snapshot.json"), encoding="utf-8"))
        d["symbols"]["TESTUSDT"]["quote_volume_24h"] = 1
        json.dump(d, open(tampered, "w", encoding="utf-8"))
        c2 = subprocess.run([py, os.path.join(ROOT, "scripts", "check_report.py"), out, "--snapshot", tampered],
                            capture_output=True, text=True)
        self.assertEqual(c2.returncode, 1)
        self.assertIn("不一致", c2.stdout)
        for p in (out, tampered):
            os.remove(p)

    def test_unknown_marker_fails(self):
        fx = os.path.join(os.path.dirname(__file__), "fixtures")
        bad = os.path.join(fx, "_bad.html")
        open(bad, "w", encoding="utf-8").write("<h2 id='s0'>x</h2><!--AUTO:nope-->")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot",
                            os.path.join(fx, "mini_snapshot.json"), "--narrative", bad, "--title", "t",
                            "--out", os.path.join(fx, "_x.html")], capture_output=True, text=True)
        os.remove(bad)
        self.assertNotEqual(r.returncode, 0)


FX = os.path.join(os.path.dirname(__file__), "fixtures")
PY = sys.executable


def _run(script, *args):
    return subprocess.run([PY, os.path.join(ROOT, "scripts", script)] + list(args), capture_output=True, text=True)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def _w(self, name, content):
        p = os.path.join(self.tmp, name)
        open(p, "w", encoding="utf-8").write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        return p

    ALL = ("<h2 id='s0'>先看结论</h2>" + "".join("<!--AUTO:%s-->" % n for n in
           ["meta", "inventory", "contracts", "constituents", "funding", "weekend", "sessions", "basis",
            "options", "fiatpairs", "bstocks", "missing"]))

    def test_build_with_empty_symbols_and_no_meta(self):
        nar = self._w("n.html", self.ALL)
        for name, snap in (("a.json", {"meta": {"fetched_at_utc": "x"}, "symbols": {}}), ("b.json", {"symbols": {}}),
                           ("c.json", {"meta": {}, "inventory": {"tradfi_total": 1}, "symbols": {"X": {"weekend": {"window_utc": ["a", "b"]}}}})):
            r = _run("build_report.py", "--snapshot", self._w(name, snap), "--narrative", nar, "--title", "t",
                     "--out", os.path.join(self.tmp, "o.html"))
            self.assertEqual(r.returncode, 0, name + r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_html_escaped(self):
        snap = {"meta": {"fetched_at_utc": "x"},
                "inventory": {"tradfi_total": 1, "by_underlying_type": {"<b>X&Y": 1},
                              "index_name_scan": [{"symbol": "<script>alert(1)</script>", "underlyingType": "a&b"}]},
                "symbols": {"<i>SYM&": {"label": "<script>L</script>", "onboard_date": "<u>",
                                        "constituents": [{"exchange": "<script>e</script>", "symbol": "A&B", "weight": 1.0}]}},
                "spot_pairs": {"<s>": {"perp": "<p>"}},
                "options": {"listed": {"<o>": {"count": 1, "expiries_utc": ["<e>"], "strike_min": 1, "strike_max": 2, "n_strikes": 1, "types": ["<t>"]}},
                            "contracts": [{"underlying": "<c>", "nakedSell": True}]},
                "bstocks_candidates": {"count": 1, "bases": ["<bb>"], "rule": "<r>", "caveat": "<cv>"},
                "missing": [{"url": "http://x/<a>", "error": "<b>"}]}
        out = os.path.join(self.tmp, "e.html")
        r = _run("build_report.py", "--snapshot", self._w("e.json", snap), "--narrative", self._w("n.html", self.ALL),
                 "--title", "<t>", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        h = open(out, encoding="utf-8").read()
        body = h.split("</style>", 1)[1]
        for bad in ("<script", "<i>SYM", "<b>X", "<s>", "<u>", "<o>", "<bb>", "<e>", "<t>", "<c>", "<a>"):
            self.assertNotIn(bad, body, bad)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", h)
        self.assertIn("A&amp;B", h)

    def test_funding_stats_empty_and_basis_none(self):
        self.assertIsNone(bd.funding_stats([]))
        self.assertIsNone(bd.basis_stats([{"t": 1, "c": 1.0}], [{"t": 2, "c": 1.0}]))

    def test_weekend_stats_zero_weekday_volume_returns_none(self):
        rows = TestWeekend()._rows()
        for r in rows:
            r["qv"] = 0.0
        s = dt.datetime(2026, 9, 25, 21, tzinfo=UTC)
        e = dt.datetime(2026, 9, 27, 22, tzinfo=UTC)
        self.assertIsNone(bd.weekend_stats(rows, s, e))

    def test_weekend_kind_by_underlying_type(self):
        self.assertEqual(bd.weekend_kind_for("COMMODITY"), "commodity")
        self.assertEqual(bd.weekend_kind_for("FX"), "fx")
        self.assertEqual(bd.weekend_kind_for("EQUITY"), "equity")
        self.assertEqual(bd.weekend_kind_for(None), "equity")

    def test_fetch_symbol_all_requests_fail_or_odd(self):
        class F:
            missing = []

            def __init__(self, resp):
                self.resp = resp

            def get(self, url, note=""):
                return self.resp.get(note)
        # 全部失败
        d = bd.fetch_symbol(F({}), "X", 10, 30)
        self.assertEqual(d, {"symbol": "X"})
        # lastFundingRate 为空串、indexPrice 为 0、资金费率与 K 线为空列表
        f = F({"premiumIndex": {"markPrice": "1", "indexPrice": "0", "lastFundingRate": "", "interestRate": "0"},
               "fundingRate": [], "klines": [], "constituents": {"constituents": [{"exchange": "a"}]}})
        d = bd.fetch_symbol(f, "X", 10, 30)
        self.assertNotIn("last_funding_rate_pct", d)
        self.assertNotIn("mark_vs_index_bp", d)
        self.assertNotIn("funding", d)
        self.assertNotIn("klines_1h", d)
        self.assertNotIn("constituents", d)
        self.assertGreaterEqual(len(f.missing), 3)   # 空列表/结构异常必须留痕

    def test_unknown_symbol_is_recorded_not_silent(self):
        # 不联网：直接验证 main 之外的约定——取数脚本对 exchangeInfo 里不存在的合约写入 missing（见 binance_data.main）
        src = open(os.path.join(ROOT, "scripts", "binance_data.py"), encoding="utf-8").read()
        self.assertIn("symbol not found", src)

    def test_check_self_closing_and_sources_heading(self):
        body = ("<h2>先看结论</h2><h2>产品清单</h2><h2>价格来源</h2><h2>谁生产的</h2><h2>玩法</h2><h2>实测</h2>"
                "<h2>坑</h2><h2>没查到的</h2><p>a<br/>b</p><span class=\"tag v\">实测</span>")
        r = _run("check_report.py", self._w("r.html", "<html><body>" + body + "</body></html>"))
        self.assertNotIn("标签不平衡", r.stdout)          # <br/> 不应误报
        self.assertIn("缺少必备章节：来源", r.stdout)       # “价格来源”不能顶替“来源”一节
        r2 = _run("check_report.py", self._w("r2.html", "<html><body>" + body + "<h2>来源</h2></body></html>"))
        self.assertNotIn("缺少必备章节", r2.stdout)

    def test_check_trad_chars_only_flag_traditional(self):
        simp = "<h2>先看结论</h2><p>后来发现现货价格对杠杆、标准、时间、为什么、这个、关于、系统、资讯、点击、产品、网络、国际</p>"
        r = _run("check_report.py", self._w("s.html", "<html><body>" + simp + "</body></html>"), "--skip-sections")
        self.assertNotIn("繁体", r.stdout)
        r2 = _run("check_report.py", self._w("t.html", "<html><body><p>幣安 價格</p></body></html>"), "--skip-sections")
        self.assertIn("繁体", r2.stdout)

    def test_check_template_leftovers_fail(self):
        r = _run("check_report.py", self._w("l.html", '<html><body><a href="URL">x</a></body></html>'), "--skip-sections")
        self.assertIn("模板占位", r.stdout)

    def test_live_without_snapshot_warns(self):
        r = _run("check_report.py", self._w("p.html", "<html><body></body></html>"), "--skip-sections", "--live")
        self.assertIn("--live 需要同时给 --snapshot", r.stdout)


class TestRegressionTradfi(unittest.TestCase):
    """回归：2026-10-03『币安衍生品业务调研』会话里，独立验收员改掉的 20 处。
    新验收器必须在报告交付前拦住其中可机检的部分。"""
    FX = os.path.join(os.path.dirname(__file__), "fixtures")

    def _load(self):
        return json.load(open(os.path.join(self.FX, "regression_tradfi_edits.json"), encoding="utf-8"))["items"]

    def _wrap(self, it):
        return {"li": "<ul>%s</ul>", "td": "<table><tr>%s</tr></table>"}.get(it["tag"], "%s") % it["html"]

    def test_old_errors_are_caught(self):
        import claims
        res = {}
        for it in self._load():
            if not it["html"]:
                continue
            iss, _ = claims.analyze(self._wrap(it))
            res[it["n"]] = ("FAIL" if any(l == "FAIL" for l, _, _ in iss) else "WARN" if iss else "NONE")
        fail = sorted(n for n, v in res.items() if v == "FAIL")
        warn = sorted(n for n, v in res.items() if v == "WARN")
        none = sorted(n for n, v in res.items() if v == "NONE")
        # 18 处真实错误（1~18；19 是验收员新增的补充说明，不是错误）里，规则能直接判 FAIL 的 13 处：
        # 无标签的数字/全称、全称否定缺范围、实测句里的手敲数字、实测+全称无快照数字证据
        # v0.2.1 起转述句必须引用来源台账：7（没出处的「不是币安自己做市」标成转述）、15（跨主体外推标成转述）、
        # 16（第三方数字标成转述）也因 G_NOCITE 被拦
        self.assertEqual(fail, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18])
        self.assertEqual(warn, [17])     # 17：推论句里手敲了数据数字（I_HANDNUM 提醒）；真正的缺陷（INDEX 合约少列）由 survey 源头补齐
        # 机检的边界，必须靠独立验收员做语义审查，或靠源头改进：
        #   14 标了「推论」的绝对化措辞（机检不判，由独立验收员审）
        #   19 验收员新增的补充说明，本身不是错误
        self.assertEqual(none, [14, 19])

    def test_compliant_rewrite_passes(self):
        py = sys.executable
        snap = os.path.join(self.FX, "mini_snapshot.json")
        nar = os.path.join(self.FX, "narrative_compliant.html")
        out = os.path.join(self.FX, "_compliant.html")
        r = subprocess.run([py, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot", snap, "--narrative", nar,
                            "--title", "t", "--out", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        c = subprocess.run([py, os.path.join(ROOT, "scripts", "check_report.py"), out, "--snapshot", snap, "--narrative", nar],
                           capture_output=True, text=True)
        os.remove(out)
        self.assertEqual(c.returncode, 0, c.stdout)

    def test_old_sentences_fail_in_full_report(self):
        """把真实的旧条目塞进完整报告，整份必须不合格。"""
        py = sys.executable
        items = "".join(it["html"] for it in self._load() if it["html"] and it["tag"] == "li")
        nar = os.path.join(self.FX, "_bad_nar.html")
        base = open(os.path.join(self.FX, "narrative_compliant.html"), encoding="utf-8").read()
        open(nar, "w", encoding="utf-8").write(base.replace("<!--AUTO:scope-->", "<ul>%s</ul>" % items))
        out = os.path.join(self.FX, "_bad.html")
        subprocess.run([py, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot", os.path.join(self.FX, "mini_snapshot.json"),
                        "--narrative", nar, "--title", "t", "--out", out], capture_output=True, text=True)
        c = subprocess.run([py, os.path.join(ROOT, "scripts", "check_report.py"), out], capture_output=True, text=True)
        for p in (nar, out):
            os.remove(p)
        self.assertEqual(c.returncode, 1)
        self.assertIn("NEG_SCOPE", c.stdout)
        self.assertIn("V_HANDNUM", c.stdout)
        self.assertIn("扫描范围声明块", c.stdout)

    def test_narrative_hash_mismatch(self):
        py = sys.executable
        snap = os.path.join(self.FX, "mini_snapshot.json")
        nar = os.path.join(self.FX, "narrative_compliant.html")
        out = os.path.join(self.FX, "_h.html")
        subprocess.run([py, os.path.join(ROOT, "scripts", "build_report.py"), "--snapshot", snap, "--narrative", nar,
                        "--title", "t", "--out", out], capture_output=True, text=True)
        other = os.path.join(self.FX, "_other.html")
        open(other, "w", encoding="utf-8").write(open(nar, encoding="utf-8").read() + "<!-- x -->")
        c = subprocess.run([py, os.path.join(ROOT, "scripts", "check_report.py"), out, "--snapshot", snap, "--narrative", other],
                           capture_output=True, text=True)
        for p in (out, other):
            os.remove(p)
        self.assertEqual(c.returncode, 1)
        self.assertIn("叙述稿不一致", c.stdout)

    def test_weekend_skip_reason_shown(self):
        import build_report
        ctx = build_report.Ctx([{"meta": {}, "symbols": {"BTCDOMUSDT": {"weekend_skipped_reason": "非 TRADIFI 永续（contractType=PERPETUAL），休市窗口不适用"}}}])
        out = build_report.g_weekend(ctx, "")
        self.assertIn("不适用", out)
        self.assertNotIn("数据缺失", out)


if __name__ == "__main__":
    unittest.main()
