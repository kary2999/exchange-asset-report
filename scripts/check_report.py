#!/usr/bin/env python3
"""报告验收：结构、占位符、标签、快照一致性，可选 --live 重新抓取抽查。退出码非 0 表示不合格。"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "hr", "img", "input", "link", "meta"}
REQUIRED_H2 = [("结论", "先看结论"), ("产品", "产品清单/有哪些"), ("价格", "价格来源"), ("谁生产|生产|发行", "谁生产的（发行/产品链）"),
               ("玩法", "各场景玩法"), ("实测|对比", "实测数据"), ("坑", "最容易踩的坑"), ("没查到|不要当已知", "没查到的"), (r"^\s*(参考|资料|数据|信息)?来源|参考资料|参考链接", "来源")]
# 只收「繁体独有、简体里不会用」的字；宁缺毋滥，避免误伤简体报告（召回有限，只是兜底提示）
TRAD = set("幣價點據義術龍產國際動體線網預測資訊證實驗請問題頁為這個們說與對從發現開關時間門還沒險標準盤倉槓約該")


class Bal(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, t, a):
        if t not in VOID:
            self.stack.append(t)

    def handle_startendtag(self, t, a):  # <br/> <div/> 自闭合：不入栈也不算多余结束标签
        pass

    def handle_endtag(self, t):
        if self.stack and self.stack[-1] == t:
            self.stack.pop()
        else:
            self.bad.append(t)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--snapshot", action="append", help="生成该报告用的 snapshot.json（可多个）")
    ap.add_argument("--live", action="store_true", help="重新抓取，核对指数成分结构与量级")
    ap.add_argument("--skip-sections", action="store_true", help="不检查必备章节（局部报告用）")
    a = ap.parse_args()
    txt = open(a.report, encoding="utf-8").read()
    fails, warns = [], []

    b = Bal()
    b.feed(txt)
    if b.stack or b.bad:
        fails.append("HTML 标签不平衡：未闭合 %s，多余 %s" % (b.stack[:5], b.bad[:5]))

    if re.search(r"<!--AUTO:|\{[VGUI]\}|@@NAV@@", txt):
        fails.append("存在未替换的占位符")
    if re.search(r'href="URL"|<h1>\s*<!--', txt):
        fails.append("存在未替换的叙述模板占位（href=\"URL\" 或空标题）")
    if "……" in re.sub(r"<style>.*?</style>", "", txt, flags=re.S):
        warns.append("正文含「……」：是不是叙述模板里的占位没替换？")
    if "数据缺失" in txt:
        warns.append("报告含「数据缺失」单元格 %d 处，需要在「没查到的」一节说明" % txt.count("数据缺失"))

    body = re.sub(r"<style>.*?</style>", "", txt, flags=re.S)
    if not a.skip_sections:
        heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", body)]
        for pat, name in REQUIRED_H2:
            if not any(re.search(pat, h) for h in heads):
                fails.append("缺少必备章节：%s" % name)

    body_nl = re.sub(r'<p class="sub">数据快照.*?</p>', "", body, flags=re.S)  # 去掉页首图例，避免图例里的标签被计入
    ntag = {k: len(re.findall(r'class="tag%s"' % v, body_nl)) for k, v in [("实测", " v"), ("转述", " g"), ("未证实", " u"), ("推论", "")]}
    if ntag["实测"] == 0:
        fails.append("没有任何「实测」标签：数字没有标注来源")
    if ntag["未证实"] == 0:
        warns.append("没有「未证实」标签：确认真的没有查不到的东西？")
    if ntag["转述"] == 0 and ntag["推论"] == 0:
        warns.append("没有「转述」「推论」标签：确认没有把二手信息当事实")

    trad = [c for c in set(body) if c in TRAD]
    if trad:
        fails.append("疑似夹杂繁体字：%s" % "".join(trad))

    # 启发式：叙述里手敲的行情数字（自动表格之外的 $、%、bp、倍）。不能证明错，只提醒核对来源。
    manual = re.sub(r"<!--AUTO-BEGIN-->.*?<!--AUTO-END-->", "", body_nl, flags=re.S)
    manual = re.sub(r"<(script|style|code)[^>]*>.*?</\1>", "", manual, flags=re.S)
    manual = re.sub(r"<[^>]+>", " ", manual)
    hits = re.findall(r"\$\s?\d[\d,\.]*\s?[亿万]?|\d[\d,\.]*\s?(?:%|bp|倍)", manual)
    if hits:
        warns.append("叙述里有 %d 处手敲数字（自动表格之外），逐个确认来自快照而非印象：%s" % (len(hits), "、".join(hits[:12])))

    m = re.search(r'name="snapshot-sha256" content="([0-9a-f]+)"', txt)
    if a.snapshot:
        h = hashlib.sha256()
        for p in a.snapshot:
            h.update(open(p, "rb").read())
        if not m:
            fails.append("报告里没有 snapshot-sha256（不是 build_report 生成的？）")
        elif m.group(1) != h.hexdigest():
            fails.append("报告与给定快照不一致（sha256 不同）：数字可能不是这份快照生成的")
    elif not m:
        warns.append("未提供 --snapshot，无法校验报告数字来源")

    if a.live and not a.snapshot:
        warns.append("--live 需要同时给 --snapshot，本次没有执行任何 live 抽查")
    if a.live and a.snapshot:
        for p in a.snapshot:
            snap = json.load(open(p, encoding="utf-8"))
            for s, d in list(snap.get("symbols", {}).items())[:6]:
                c = get("https://fapi.binance.com/fapi/v1/constituents?symbol=" + s)
                if not c:
                    warns.append("live：%s constituents 抓取失败" % s)
                    continue
                old = {(x["exchange"], x["symbol"]): x["weight"] for x in d.get("constituents", [])}
                if not old:
                    continue  # 快照里本来就没有成分（或该合约没有指数成分），无从比较
                try:
                    now = {(x["exchange"], x["symbol"]): float(x["weight"]) for x in c["constituents"]}
                except (KeyError, TypeError, ValueError):
                    warns.append("live：%s constituents 返回结构异常，未比较" % s)
                    continue
                if set(now) != set(old):
                    fails.append("live：%s 指数成分来源集合已变化（结构性变化，报告需更新）" % s)
                elif any(abs(now[k] - old[k]) > 0.01 for k in now):
                    warns.append("live：%s 指数权重变化超过 1 个百分点" % s)
                t = get("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=" + s)
                if t and t.get("quoteVolume") and d.get("quote_volume_24h"):
                    r = float(t["quoteVolume"]) / d["quote_volume_24h"]
                    if r > 3 or r < 1 / 3:
                        warns.append("live：%s 24h 成交额与快照相差 %.1f 倍（量级漂移，正文若引用需重取）" % (s, r))

    print("标签统计：", ntag)
    for w in warns:
        print("WARN ", w)
    for f in fails:
        print("FAIL ", f)
    print("结果：%s（FAIL %d / WARN %d）" % ("不合格" if fails else "通过", len(fails), len(warns)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
