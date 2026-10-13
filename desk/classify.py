# -*- coding: utf-8 -*-
"""宏观 / 行业 / 个股 分层 + 情绪 + 动作抽取。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexicon import (
    BEARISH,
    BULLISH,
    INDUSTRIES,
    MACRO_TERMS,
    SOURCE_WEIGHT,
    STANCE_PATTERNS,
    TICKERS,
)
from .parser import RawSnippet

ACTION_SWITCH = re.compile(
    r"(?:把)?(?P<frm>[\u4e00-\u9fffA-Za-z0-9]{2,12})(?:换到|换成)(?P<to>[\u4e00-\u9fffA-Za-z0-9]{2,12})"
)
COST_DONE_LINE = re.compile(r"([^\n。；]{2,40})的成本做完了")
COST_DOING = re.compile(r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,8})还在做")


@dataclass
class Classified:
    source: str
    text: str
    layer: str
    layers: list[str]
    industries: list[str]
    tickers: list[str]
    sentiment: float
    stance: str
    mood: str
    actions: list[dict]
    weight: float
    order: int = 0
    extras: dict = field(default_factory=dict)


def _hits(text: str, terms: list[str]) -> list[str]:
    found = []
    for t in terms:
        if t and t in text:
            found.append(t)
    return found


def extract_tickers(text: str) -> list[str]:
    out: list[str] = []
    for canon, aliases in TICKERS.items():
        for a in aliases:
            if a and a in text:
                out.append(canon)
                break
    # 避免「300」误伤：只有出现沪深300/300在 才收
    if "沪深300" not in text and not re.search(r"(?<!\d)300(?!\d)", text):
        out = [x for x in out if x != "沪深300"]
    # 「科创」单独出现很常见，保留
    uniq: list[str] = []
    for x in out:
        if x not in uniq:
            uniq.append(x)
    return uniq


def extract_industries(text: str) -> list[str]:
    out: list[str] = []
    for name, aliases in INDUSTRIES.items():
        if any(a and a in text for a in aliases):
            out.append(name)
    return out


def _unnegated(text: str, word: str) -> bool:
    return any(not re.search(r"(?:不|未|没|无|避免|不要|不能|不再|并非|不是)$", text[max(0, m.start()-4):m.start()])
               for m in re.finditer(re.escape(word), text))


def score_sentiment(text: str) -> float:
    score = 0.0
    occupied: set[int] = set()
    terms = [(w,v) for w,v in BULLISH] + [("看好",0.7)] + [(w,-v) for w,v in BEARISH]
    for word, value in sorted(terms, key=lambda x: -len(x[0])):
        for match in re.finditer(re.escape(word), text):
            span = set(range(*match.span()))
            if occupied & span:
                continue
            occupied.update(span)
            if _unnegated(text[max(0,match.start()-4):match.end()], word):
                score += value
                break
    return round(max(-2.2, min(2.2, score)) / 2.2, 3)


def mood_label(score: float) -> str:
    if score >= 0.28:
        return "偏多"
    if score <= -0.28:
        return "偏空"
    if abs(score) >= 0.12:
        return "分歧"
    return "中性"


def extract_stance(text: str) -> str:
    for needle, stance in STANCE_PATTERNS:
        if _unnegated(text, needle):
            return stance
    return "hold"


def extract_actions(text: str) -> list[dict]:
    actions: list[dict] = []
    known_names = set(TICKERS) | {a for aliases in TICKERS.values() for a in aliases}
    for m in ACTION_SWITCH.finditer(text):
        frm, to = m.group("frm"), m.group("to")
        if frm not in known_names and to not in known_names:
            continue
        actions.append(
            {
                "action": "switch",
                "from": frm,
                "ticker": to,
                "note": m.group(0),
            }
        )
    for m in COST_DONE_LINE.finditer(text):
        chunk = m.group(1)
        chunk = re.sub(r"^(?:pcb|PCB|光)[:：]\s*", "", chunk)
        names = [n for n in re.split(r"[\s、,，]+", chunk) if 2 <= len(n) <= 8]
        for n in names or [chunk[-4:]]:
            actions.append(
                {
                    "action": "cost_done",
                    "ticker": n,
                    "note": "作者称成本做完，非操作建议",
                }
            )
    for m in COST_DOING.finditer(text):
        actions.append(
            {
                "action": "working",
                "ticker": m.group("name"),
                "note": "成本还在做",
            }
        )
    if "观察" in text:
        for t in extract_tickers(text):
            actions.append({"action": "watch", "ticker": t, "note": "观察"})
    if _unnegated(text, "重仓"):
        for t in extract_tickers(text):
            actions.append({"action": "add", "ticker": t, "note": "重仓"})
    if any(_unnegated(text, x) for x in ("出掉", "清了", "减仓", "减到剩皮毛")):
        for t in extract_tickers(text):
            actions.append({"action": "reduce", "ticker": t, "note": "减/清"})
    # 去重
    seen = set()
    uniq = []
    for a in actions:
        key = (a.get("action"), a.get("ticker"), a.get("from"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    return uniq


def classify_snippet(snip: RawSnippet) -> Classified:
    text = snip.text
    tickers = extract_tickers(text)
    industries = extract_industries(text)
    macro_hits = _hits(text, MACRO_TERMS)
    sent = score_sentiment(text)

    scores = {
        "macro": 1.6 * len(macro_hits) + (0.4 if any(k in text for k in ("美", "利率", "债", "油")) else 0),
        "industry": 1.4 * len(industries),
        "stock": 1.8 * len(tickers),
    }
    # 策略口吻更偏仓位
    if snip.source == "策略":
        scores["stock"] += 0.4
        scores["industry"] += 0.2
    if snip.source in {"姜汁汽水", "付鹏", "VITO"} and macro_hits:
        scores["macro"] += 0.8

    layer = max(scores, key=scores.get)
    if scores[layer] == 0:
        layer = "macro" if len(text) > 80 else "industry"

    layers = [k for k, v in scores.items() if v > 0]
    if layer not in layers:
        layers.insert(0, layer)
    else:
        layers = [layer] + [x for x in layers if x != layer]

    return Classified(
        source=snip.source,
        text=text,
        layer=layer,
        layers=layers,
        industries=industries,
        tickers=tickers,
        sentiment=sent,
        stance=extract_stance(text),
        mood=mood_label(sent),
        actions=extract_actions(text),
        weight=1.0,
        order=snip.order,
        extras={"macro_hits": macro_hits, "scores": scores,
                "classification_engine": "rule", "classification_note": "规则关键词归类，未使用模型；零情绪分不代表确定中性。",
                "confidence": "medium" if max(scores.values()) else "low",
                "viewpoint": text[:240], "conditional_plans": []},
    )
