# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from . import expand
from .ai import classify_batch, validate_conclusion
from .classify import Classified, classify_snippet
from .db import append_day, get_day, save_conclusion, snippet_key
from .parser import parse_notes
from .strategy import llm_refine_prompt, rule_conclude


def classified_to_dict(c: Classified) -> dict:
    return asdict(c)


def analyze_day(day: str | dict, use_llm: bool = True) -> dict:
    key = day if isinstance(day,str) else day["date"]
    packed = get_day(key)
    if not packed:
        raise KeyError(f"没有这一天：{key}")
    draft = rule_conclude(packed)
    # Persist a usable fallback before the optional network request.
    saved = save_conclusion(key,draft,expected_updated_at=packed["updated_at"])
    if not use_llm or saved["snippets"] != packed["snippets"]:
        return saved
    try:
        result = expand.conclude_with_llm(llm_refine_prompt(packed,draft))
        conclusion = {**draft,**validate_conclusion(result,packed),"rule_backup":draft}
    except Exception as exc:
        conclusion = {**draft,"fallback_note":f"模型综合未完成（{type(exc).__name__}），已保存原文并使用规则结论。"}
        conclusion["note"] += " " + conclusion["fallback_note"]
    # Never overwrite a mutation that happened while the model was running.
    return save_conclusion(key,conclusion,expected_updated_at=saved["updated_at"])


def ingest_text(text: str, fallback: date | None = None, use_llm: bool = True) -> list[str]:
    if not isinstance(text,str) or not text.strip():
        raise ValueError("请输入需要整理的原文。")
    if len(text)>500000:
        raise ValueError("单次原文最多50万字，请分批投入。")
    days = parse_notes(text,fallback_day=fallback)  # Validate every date before any writes.
    if not days:
        raise ValueError("没有可保存的正文，请在日期或来源标题下加入内容。")
    pending, all_new = [], []
    for raw_day in days:
        key = raw_day.day.isoformat()
        existing = get_day(key)
        seen = set()
        for s in (existing or {}).get("snippets",[]):
            seen.add(snippet_key(s["source"],s["text"]))
            original = s.get("extras",{}).get("ingest_key")
            if isinstance(original,list) and len(original)==2:
                seen.add(tuple(original))
        new = []
        for s in raw_day.snippets:
            identity = snippet_key(s.source,s.text)
            if identity in seen:
                continue
            seen.add(identity)
            new.append(classified_to_dict(classify_snippet(s)))
        pending.append((key,raw_day,new))
        all_new.extend(new)
    classification_failed = False
    if use_llm and all_new:
        try:
            classify_batch(all_new)
        except Exception as exc:
            classification_failed = True
            for c in all_new:
                c["extras"]["classification_note"] = f"模型分类未完成（{type(exc).__name__}）；已保留原文，使用规则归类。"
    changed = []
    for key,raw_day,new in pending:
        if new and append_day(key,raw_day.raw,new):
            changed.append(key)
            analyze_day(key,use_llm=False)
    # At most one classification and one synthesis per import, regardless of date count.
    if use_llm and changed and not classification_failed:
        analyze_day(changed[-1],use_llm=True)
    for key in changed:
        if classification_failed or (use_llm and key != changed[-1]):
            packed = get_day(key)
            c = dict(packed["conclusion"])
            c["fallback_note"] = ("模型分类失败，本次未再次请求综合；原文已保存，当前为规则结论。" if classification_failed
                                  else "多日期导入仅对最新变更日期调用模型综合，本日为规则结论。")
            c["note"] += " " + c["fallback_note"]
            save_conclusion(key,c,expected_updated_at=packed["updated_at"])
    return [key for key,_,_ in pending]


def mood_summary(day: dict) -> dict:
    draft = rule_conclude(day)
    return {"overall":draft["overall_mood"],"layers":draft["mood"],"scores":draft["mood_scores"]}
