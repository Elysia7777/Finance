#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【B · 后端与数据】去重校验（框架占位，🚧 待开发）
─────────────────────────────────────────────────
职责：对导入的流水做去重校验，避免同一笔交易重复入库。
TODO(B组): 定义重复判定键（如 日期+金额+摘要）与冲突处理策略。
"""


def dedup(records, existing=None):
    existing = existing or []
    seen = set()
    for r in existing:
        seen.add((r.get("date"), r.get("amount"), r.get("note")))

    added, duplicated = [], []
    for r in records:
        key = (r.get("date"), r.get("amount"), r.get("note"))
        if key in seen:
            duplicated.append(r)
        else:
            seen.add(key)
            added.append(r)
    return added, duplicated
