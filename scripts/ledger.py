#!/usr/bin/env python3
"""来源台账：把「转述」句子钉在抓到的原文上。

问题背景（2026-10 回归）：独立验收员在新报告里仍挑出 28 处问题，大多是「标了转述但原文没这句」「把几个合约的规则外推到全部」。
做法：每个 {G} 句必须引用台账里的来源，并给出一个原文片段；build_report 会核对
  1) 来源存在，且是脚本直接抓到的原文（status=ok, origin=raw）；
  2) 片段确实出现在抓到的原文里（忽略大小写和空白差异）。
抓不到原文的来源（403/JS 渲染/被拦）只允许写 {U}。

命令：
  python3 scripts/ledger.py add  --ledger L.json --id pr_options --url URL [--covers "这个来源讲的范围"] [--title T]
  python3 scripts/ledger.py list --ledger L.json
  python3 scripts/ledger.py grep --ledger L.json --id pr_options --q "Alpaca"      # 看原文里有没有、上下文是什么
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
BLOCK_MARKERS = re.compile(r"just a moment|cloudflare|access denied|enable javascript|attention required|captcha|verify you are human", re.I)
MIN_TEXT = 600  # 低于这个长度基本是 JS 外壳或拦截页


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw):
    p = _Text()
    try:
        p.feed(raw)
    except Exception:
        pass
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(p.parts))).strip()


def norm(s):
    """比较用：小写、统一引号/破折号、去零宽字符、折叠空白。"""
    s = s.lower()
    s = s.replace("​", "").replace("﻿", "")
    s = re.sub(r"[‘’“”\"'`]", "", s)
    s = re.sub(r"[–—−-]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def load(path):
    if not os.path.exists(path):
        return {"sources": {}}
    return json.load(open(path, encoding="utf-8"))


def save(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def text_path(ledger_path, sid):
    return os.path.join(os.path.dirname(os.path.abspath(ledger_path)), "sources", "%s.txt" % sid)


def fetch_raw(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                                               "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, ctype, body, ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", body, "HTTP %s" % e.code
    except Exception as e:
        return 0, "", b"", str(e)[:200]


def add_source(ledger_path, sid, url, covers="", title="", fetcher=fetch_raw):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", sid):
        raise ValueError("id 只能含字母数字下划线连字符：%r" % sid)
    code, ctype, body, err = fetcher(url)
    text = ""
    if body:
        raw = body.decode("utf-8", errors="replace")
        text = html_to_text(raw) if ("html" in ctype.lower() or raw.lstrip().startswith("<")) else raw
    if code == 200 and BLOCK_MARKERS.search(text[:3000]) and len(text) < 4000:
        status = "blocked"
    elif code == 200 and len(text) >= MIN_TEXT:
        status = "ok"
    elif code == 200:
        status = "empty"        # 200 但几乎没有正文：多半是 JS 渲染，抓不到原文
    elif code in (401, 403, 429, 451):
        status = "blocked"
    elif code == 0:
        status = "error"
    else:
        status = "error"
    entry = {"url": url, "title": title or url, "covers": covers, "status": status, "http_code": code,
             "origin": "raw", "text_chars": len(text), "raw_sha256": hashlib.sha256(body).hexdigest() if body else "",
             "fetched_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "error": err}
    data = load(ledger_path)
    data.setdefault("sources", {})[sid] = entry
    save(ledger_path, data)
    if status == "ok":
        tp = text_path(ledger_path, sid)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w", encoding="utf-8").write(text)
    return entry


def get_text(ledger_path, sid):
    tp = text_path(ledger_path, sid)
    return open(tp, encoding="utf-8").read() if os.path.exists(tp) else ""


def verify_cite(ledger, ledger_path, sid, quote, tag):
    """返回 (ok, 说明)。tag 为 'G' 或 'U'。"""
    src = ledger.get("sources", {}).get(sid)
    if src is None:
        return False, "来源 %r 不在台账里" % sid
    if tag == "U":
        return True, ""
    if src.get("origin") != "raw" or src.get("status") != "ok":
        return False, "来源 %r 状态是 %s（%s），没有抓到原文，只能写 {U}" % (sid, src.get("status"), src.get("error") or src.get("http_code"))
    if not quote:
        return False, "转述必须给原文片段：写成 {G:%s|原文里的一句话或关键短语}" % sid
    if norm(quote) not in norm(get_text(ledger_path, sid)):
        return False, "来源 %r 的原文里找不到片段 %r" % (sid, quote)
    return True, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("add")
    a1.add_argument("--ledger", required=True)
    a1.add_argument("--id", required=True)
    a1.add_argument("--url", required=True)
    a1.add_argument("--covers", default="")
    a1.add_argument("--title", default="")
    a2 = sub.add_parser("list")
    a2.add_argument("--ledger", required=True)
    a3 = sub.add_parser("grep")
    a3.add_argument("--ledger", required=True)
    a3.add_argument("--id", required=True)
    a3.add_argument("--q", required=True)
    a = ap.parse_args()
    if a.cmd == "add":
        e = add_source(a.ledger, a.id, a.url, a.covers, a.title)
        print("%s  %s  http=%s  chars=%d  %s" % (a.id, e["status"], e["http_code"], e["text_chars"], e["error"]))
        if e["status"] != "ok":
            print("  ↑ 没有抓到原文：引用这个来源的句子只能写 {U}", file=sys.stderr)
    elif a.cmd == "list":
        for sid, e in load(a.ledger).get("sources", {}).items():
            print("%-24s %-8s http=%-4s chars=%-7d %s" % (sid, e["status"], e["http_code"], e["text_chars"], e["url"]))
    elif a.cmd == "grep":
        t = get_text(a.ledger, a.id)
        nt = norm(t)
        q = norm(a.q)
        i = nt.find(q)
        if i < 0:
            print("未找到")
            sys.exit(1)
        print("…%s…" % nt[max(0, i - 150): i + len(q) + 150])


if __name__ == "__main__":
    main()
