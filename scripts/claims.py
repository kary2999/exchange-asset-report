#!/usr/bin/env python3
"""断言级分析：把叙述拆成句子，逐句检查来源标签与措辞。

只分析「手写叙述」。自动生成的表格（AUTO-BEGIN..AUTO-END）、导航、标题、代码、页脚/来源（class="sub"）跳过。
叙述里 {{v:..}}/{{i:..}} 渲染出的数字（<span class="val">）视为已取自快照，不算手敲。

规则（FAIL 表示不合格，WARN 表示提醒）：
  UNTAGGED_STRICT  FAIL  含数字、全称/否定词的句子没有任何标签
  UNTAGGED_SOFT    WARN  含「币安…/发行/托管/监管/杠杆/门槛…」的句子没有标签
  NEG_SCOPE        FAIL  「没有/不存在 X」类否定结论没有写扫描范围（接口/扫描/没查到/…）
  NEG_GLOBAL_V     FAIL  实测句里把否定推到全站/全球站
  V_INFER          FAIL  标了实测却含推测措辞（我判断/通常/可能/…）
  V_HANDNUM        FAIL  标了实测却含手敲数字（实测数字必须来自 {{...}} 占位）
  WEBONLY_V        FAIL  在 class="webonly"（仅网页转述区）里出现实测标签
  V_UNIVERSAL_NOVAL FAIL 标了实测、含全称/否定词，却没有 {{...}} 快照数字作证据
  G_NOCITE         FAIL  转述句没有引用来源台账（{G:来源id|原文片段}）
  G_GENERALIZE     WARN  转述句含「所有/全部/每个/都是」等泛化词：同时打印来源的覆盖范围，提醒核对是否外推
  I_EXPLAIN        FAIL  推论里在解释原因（因为/所以/导致），却没有「猜测/没验证/可能」等限定
  I_HANDNUM        WARN  推论句里有手敲数据数字，确认不是快照数据
  GU_MIX           WARN  同一句同时标转述和未证实（通常是两个事实混在一句，拆开）
  THIRDPARTY       WARN  提到第三方/教程/博客/论坛，却只标转述没标未证实
"""
import re
import sys
from html.parser import HTMLParser

TAGCHAR = {"v": "V", "g": "G", "u": "U"}
SKIP_TAGS = {"nav", "h1", "h2", "style", "script", "details", "title", "head"}
ITEM_TAGS = {"li", "p", "td", "h3"}
VOID = {"area", "base", "br", "col", "hr", "img", "input", "link", "meta"}

STRICT_WORDS = re.compile(r"没有|不存在|只有|全部|所有|唯一|均为|都是|从不|必须|绝不|总是")
SOFT_RE = re.compile(r"币安(自己|是|会|将|已|称|提供|支持|发行|允许|要求|有)|发行|托管|监管|牌照|杠杆|门槛|做市|对手方|清算|交割")
NEG_RE = re.compile(r"(币安|交易所|全站|全球站|平台|官方)[^。；，]{0,10}(没有|不存在|未提供|不提供|不支持|没提供)"
                    r"|(没有|不存在|没有提供|不提供)[^。；，]{0,24}(合约|产品|CFD|指数|期货|业务|接口|牌照)")
SCOPE_RE = re.compile(r"接口|扫描|没查到|没找到|没有找到|未找到|未扫|没扫|范围|新闻稿|公告|只能说|没读到|没有读到")
GLOBAL_RE = re.compile(r"全站|全球站|币安全站|整个币安|币安没有|币安不存在|币安不提供")
INF_RE = re.compile(r"我(判断|推测|推断|认为|猜)|推测|推断|应该|通常|大概|可能|似乎|一般来说|估计|理解为|看起来|倾向")
GEN_RE = re.compile(r"所有(?!权)|全部|各品种|每个|均为|都是|整个|全站|任何|只有|仅有|唯一")
# 「所以」是普通的逻辑连接词，会把很多推导句误判成解释原因（回归实测的假阳性），因此不放进来；只抓明确的因果解释标记
EXPLAIN_RE = re.compile(r"因为|由于|导致|原因是|解释[:：是]|我的解释")
HEDGE_RE = re.compile(r"猜测|推断|没验证|未验证|可能|看起来|我的解释|倾向|估计|不确定")
THIRD_RE = re.compile(r"第三方|教程|博客|论坛|二手|自媒体|搜索摘要")
EXEMPT_SENT = re.compile(r"不是投资建议|不构成|本报告|以下为|见下表|见上表|见第\s?\d+\s?节|详见|如下")
NUM_STRIP = re.compile(r"标普\s?500|S&P\s?500|纳指\s?100|纳斯达克\s?100|罗素\s?2000|KOSPI\s?200|Nasdaq-?100|MSCI|§CODE§|24/7|24/5|1:1|T\+1|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|第\s?\d+\s?(?:节|条|项)|[A-Za-z]+-\d+|\d+\.\d+\.\d+")
NUM_RE = re.compile(r"\$\s?\d|\d[\d,\.]*\s?(?:%|bp|倍|个|只|档|万|亿|美元|桶|天|期|次|家|条|种)|(?<![A-Za-z\-_/\.])\d{2,}(?![A-Za-z])")


class Items(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []        # (tag, skip, webonly)
        self.items = []        # 已完成条目
        self.cur = []          # 条目栈
        self.auto = 0
        self.suppress = 0      # 标签 span / val / code 内部文字不收集
        self.supp_stack = []

    def _skip(self):
        return any(s for _, s, _ in self.stack)

    def _web(self):
        return any(w for _, _, w in self.stack)

    def handle_comment(self, data):
        if data.strip() == "AUTO-BEGIN":
            self.auto += 1
        elif data.strip() == "AUTO-END":
            self.auto = max(0, self.auto - 1)

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        cls = dict(attrs).get("class", "") or ""
        classes = cls.split()
        skip = tag in SKIP_TAGS or "sub" in classes or "auto" in classes
        web = "webonly" in classes
        self.stack.append((tag, skip, web))
        if tag in ITEM_TAGS:
            self.cur.append({"parts": [], "tags": [], "web": self._web(), "auto": self.auto > 0, "skip": self._skip()})
        if tag == "span" and "tag" in classes:
            t = next((TAGCHAR[c] for c in classes if c in TAGCHAR), "I")
            if self.cur:
                self.cur[-1]["parts"].append("⟦%s⟧" % t)
                ad = dict(attrs)
                if ad.get("data-cite"):
                    self.cur[-1]["parts"].append("⟦C⟧")
                    self.cur[-1].setdefault("covers", []).append(ad.get("data-covers", ""))
            self.supp_stack.append(("span", True))
            self.suppress += 1
        elif tag == "span" and "val" in classes:
            if self.cur:
                self.cur[-1]["parts"].append("§VAL§")
            self.supp_stack.append(("span", True))
            self.suppress += 1
        elif tag == "code":
            if self.cur:
                self.cur[-1]["parts"].append("§CODE§")
            self.supp_stack.append(("code", True))
            self.suppress += 1
        elif tag in ("span", "code"):
            self.supp_stack.append((tag, False))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # 弹出到匹配的 tag
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in ("span", "code") and self.supp_stack:
            for i in range(len(self.supp_stack) - 1, -1, -1):
                if self.supp_stack[i][0] == tag:
                    if self.supp_stack[i][1]:
                        self.suppress -= 1
                    del self.supp_stack[i:]
                    break
        if tag in ITEM_TAGS and self.cur:
            it = self.cur.pop()
            if not it["skip"] and not it["auto"]:
                self.items.append(it)

    def handle_data(self, data):
        if self.cur and not self.suppress:
            self.cur[-1]["parts"].append(data)


def split_sentences(parts):
    text = "".join(parts)
    pieces = re.split(r"(?<=[。！？])", text)  # 分号不拆句：同一句里的分句共用句末标签
    out = []
    for i, p in enumerate(pieces):
        if i > 0:
            m = re.match(r"^((?:⟦[VGUIC]⟧)+)", p)
            if m and out:
                out[-1] += m.group(1)
                p = p[len(m.group(1)):]
        out.append(p)
    return [p for p in out if re.sub(r"⟦[VGUIC]⟧|\s", "", p)]


def analyze(html_text):
    """返回 (issues, stats)。issues: [(level, rule, sentence)]"""
    p = Items()
    p.feed(html_text)
    issues, n_sent, n_claim, n_tagged = [], 0, 0, 0
    for it in p.items:
        for s in split_sentences(it["parts"]):
            tags = set(re.findall(r"⟦([VGUIC])⟧", s))
            plain = re.sub(r"⟦[VGUIC]⟧", "", s).strip()
            if len(plain) < 6:
                continue
            n_sent += 1
            if EXEMPT_SENT.search(plain):
                continue
            body = NUM_STRIP.sub(" ", plain.replace("§VAL§", "§VAL§"))
            has_val = "§VAL§" in plain
            hand_num = bool(NUM_RE.search(body))
            strict = has_val or hand_num or bool(STRICT_WORDS.search(plain))
            soft = bool(SOFT_RE.search(plain))
            show = re.sub(r"§CODE§", "…", plain)[:140]
            if strict or soft:
                n_claim += 1
                if tags:
                    n_tagged += 1
            if strict and not tags:
                issues.append(("FAIL", "UNTAGGED_STRICT", show))
            elif soft and not tags:
                issues.append(("WARN", "UNTAGGED_SOFT", show))
            neg = bool(NEG_RE.search(plain))
            if neg and not SCOPE_RE.search(plain):
                issues.append(("FAIL", "NEG_SCOPE", show))
            if "V" in tags:
                if neg and GLOBAL_RE.search(plain):
                    issues.append(("FAIL", "NEG_GLOBAL_V", show))
                if INF_RE.search(plain):
                    issues.append(("FAIL", "V_INFER", show))
                if hand_num:
                    issues.append(("FAIL", "V_HANDNUM", show))
                if STRICT_WORDS.search(plain) and not has_val:
                    issues.append(("FAIL", "V_UNIVERSAL_NOVAL", show))
                if it["web"]:
                    issues.append(("FAIL", "WEBONLY_V", show))
            if "G" in tags and "C" not in tags:
                issues.append(("FAIL", "G_NOCITE", show))
            if "G" in tags and GEN_RE.search(plain):
                issues.append(("WARN", "G_GENERALIZE", show + "  ｜来源覆盖范围：" + ("；".join(c for c in it.get("covers", []) if c) or "未记录")))
            if "I" in tags and "V" not in tags and "G" not in tags:
                if EXPLAIN_RE.search(plain) and not HEDGE_RE.search(plain):
                    issues.append(("FAIL", "I_EXPLAIN", show))
                if hand_num:
                    issues.append(("WARN", "I_HANDNUM", show))
            if "G" in tags and "U" in tags:
                issues.append(("WARN", "GU_MIX", show))
            if THIRD_RE.search(plain) and "G" in tags and "U" not in tags:
                issues.append(("WARN", "THIRDPARTY", show))
    has_neg = any(r in ("NEG_SCOPE",) for _, r, _ in issues) or any(
        NEG_RE.search(re.sub(r"⟦[VGUIC]⟧", "", "".join(it["parts"]))) for it in p.items)
    return issues, {"sentences": n_sent, "claim_sentences": n_claim, "tagged_claims": n_tagged, "has_negative_claims": has_neg}


if __name__ == "__main__":
    iss, st = analyze(open(sys.argv[1], encoding="utf-8").read())
    print(st)
    for lv, r, s in iss:
        print(lv, r, s)
