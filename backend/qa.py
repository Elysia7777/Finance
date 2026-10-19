#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic grounded Q&A and spending reminders over local transactions.

金额与汇总全部由本模块的代码计算；类别、期间用于筛选，绝不编造数字。
不发起任何网络请求，不依赖第三方库。
"""

import re
from datetime import datetime

_FIXED_CATEGORIES = ("餐饮", "交通", "医疗", "购物", "娱乐",
                     "大型开支", "月度开支", "日常开支", "收入")

_CATEGORY_ALIASES = {
    "餐饮": ("餐", "吃", "饭", "外卖", "奶茶", "咖啡", "食堂", "超市", "早餐", "午餐", "晚餐"),
    "交通": ("交通", "出行", "打车", "地铁", "公交", "滴滴", "加油", "停车", "高铁", "火车"),
    "医疗": ("医疗", "药", "医院", "诊所", "看病"),
    "购物": ("购物", "买", "淘宝", "京东", "拼多多", "商场", "网购"),
    "娱乐": ("娱乐", "电影", "游戏", "演出"),
    "月度开支": ("月度", "房租", "水电", "电费", "话费", "物业", "宽带"),
    "大型开支": ("大型", "大额"),
}


def _fmt(value):
    return "¥%s" % format(round(float(value or 0), 2), ",.2f")


def _resolve_period(question):
    match = re.search(r"(20\d{2})\s*[-年]\s*(\d{1,2})", question)
    if match:
        return "%04d-%02d" % (int(match.group(1)), int(match.group(2)))
    if any(word in question for word in ("全部", "所有", "总共", "一共", "累计", "历史", "今年")):
        return None
    now = datetime.now()
    if any(word in question for word in ("上个月", "上月")):
        year, month = now.year, now.month - 1
        if month == 0:
            year, month = year - 1, 12
        return "%04d-%02d" % (year, month)
    return "%04d-%02d" % (now.year, now.month)


def _period_label(period):
    if not period:
        return "全部时间"
    year, month = period.split("-")
    return "%d年%d月" % (int(year), int(month))


def _detect_category(question, transactions):
    categories = list(dict.fromkeys(
        list(_FIXED_CATEGORIES) +
        [str(transaction.get("category") or "") for transaction in transactions]
    ))
    for category in categories:
        if category and category in question:
            return category
    for category, aliases in _CATEGORY_ALIASES.items():
        if category in categories and any(alias in question for alias in aliases):
            return category
    return None


def _metric(question):
    if any(word in question for word in ("结余", "余额", "剩多少", "净额")):
        return "balance"
    if any(word in question for word in ("收入", "赚了", "进账", "工资")):
        return "income"
    if any(word in question for word in ("几笔", "多少笔", "笔数", "几次")):
        return "count"
    if any(word in question for word in ("最多", "最高", "最大", "哪类", "哪个分类", "大头")):
        return "top"
    if any(word in question for word in ("占比", "比例", "占多少", "比重")):
        return "share"
    if any(word in question for word in ("平均", "每天", "日均")):
        return "average"
    return "expense"


def _evidence(records):
    return [{
        "id": record.get("id", ""),
        "date": record.get("date", ""),
        "amount": record.get("amount", 0),
        "direction": record.get("direction", ""),
        "category": record.get("category", ""),
        "merchant": record.get("merchant", ""),
        "note": record.get("note", ""),
    } for record in records][:20]


def _result(question, metric, period, category, text, records):
    return {
        "question": question,
        "answer": text,
        "metric": metric,
        "period": period,
        "periodLabel": _period_label(period),
        "category": category,
        "evidence": _evidence(records),
    }


def answer(question, transactions, settings=None):
    transactions = transactions or []
    question = (question or "").strip()
    if not question:
        raise ValueError("请先输入问题")

    period = _resolve_period(question)
    metric = _metric(question)
    category = _detect_category(question, transactions)
    label = _period_label(period)

    if not transactions:
        return _result(question, metric, period, category,
                       "还没有导入任何账单。先去「总览」页上传微信账单，再回来问我吧。", [])

    def filtered(direction):
        rows = transactions
        if period:
            rows = [t for t in rows if str(t.get("date", "")).startswith(period)]
        if category:
            rows = [t for t in rows if t.get("category") == category]
        if direction:
            rows = [t for t in rows if t.get("direction") == direction]
        return rows

    expenses = filtered("expense")
    total = sum(t["amount"] for t in expenses)

    if metric == "balance":
        income = sum(t["amount"] for t in filtered("income"))
        text = "%s结余 %s（收入 %s − 支出 %s）" % (label, _fmt(income - total), _fmt(income), _fmt(total))
        return _result(question, metric, period, category, text, filtered(None))

    if metric == "income":
        rows = filtered("income")
        total_income = sum(t["amount"] for t in rows)
        text = "%s收入 %s，共 %d 笔" % (label, _fmt(total_income), len(rows))
        return _result(question, metric, period, category, text, rows)

    if metric == "count":
        rows = filtered("expense") if category else filtered(None)
        prefix = "「%s」" % category if category else ""
        text = "%s%s共 %d 笔" % (label, prefix, len(rows))
        return _result(question, metric, period, category, text, rows)

    if metric == "top":
        if not expenses:
            return _result(question, metric, period, category, "%s没有支出记录" % label, [])
        grouped = {}
        for t in expenses:
            name = t.get("category") or "未分类"
            grouped[name] = grouped.get(name, 0) + t["amount"]
        top_name, top_amount = max(grouped.items(), key=lambda kv: kv[1])
        top_records = [t for t in expenses if (t.get("category") or "未分类") == top_name]
        return _result(question, metric, period, category,
                       "%s支出最高的是「%s」，共 %s" % (label, top_name, _fmt(top_amount)), top_records)

    if metric == "share":
        cat_records = [t for t in expenses if t.get("category") == category] if category else expenses
        cat_total = sum(t["amount"] for t in cat_records)
        if not total:
            return _result(question, metric, period, category, "%s没有支出记录，无法计算占比" % label, [])
        share = cat_total / total * 100
        name = category or "全部"
        return _result(question, metric, period, category,
                       "%s「%s」支出 %s，占支出 %.1f%%" % (label, name, _fmt(cat_total), share), cat_records)

    if metric == "average":
        if not expenses:
            return _result(question, metric, period, category, "%s没有支出记录" % label, [])
        days = len({t["date"] for t in expenses})
        return _result(question, metric, period, category,
                       "%s共支出 %s，有支出的天数 %d 天，平均每天 %s" %
                       (label, _fmt(total), days, _fmt(total / max(days, 1))), expenses)

    if category:
        cat_records = [t for t in expenses if t.get("category") == category]
        cat_total = sum(t["amount"] for t in cat_records)
        return _result(question, metric, period, category,
                       "%s「%s」支出 %s，共 %d 笔" % (label, category, _fmt(cat_total), len(cat_records)), cat_records)

    return _result(question, metric, period, category,
                   "%s共支出 %s，共 %d 笔" % (label, _fmt(total), len(expenses)), expenses)


def build_reminders(transactions, settings=None):
    settings = settings or {}
    transactions = transactions or []
    reminders = []
    now = datetime.now()
    this_month = "%04d-%02d" % (now.year, now.month)
    year, month = now.year, now.month - 1
    if month == 0:
        year, month = year - 1, 12
    prev_month = "%04d-%02d" % (year, month)

    expenses = [t for t in transactions if t.get("direction") == "expense"]
    this_expenses = [t for t in expenses if str(t.get("date", "")).startswith(this_month)]
    this_total = sum(t["amount"] for t in this_expenses)

    threshold = float(settings.get("largeThreshold") or 1000)
    large = sorted([t for t in this_expenses if t["amount"] >= threshold],
                   key=lambda t: -t["amount"])[:5]
    for t in large:
        reminders.append({
            "level": "warn",
            "title": "大额支出",
            "text": "%s %s 支出 %s" % (t["date"], t.get("merchant") or t.get("category") or "未命名", _fmt(t["amount"])),
        })

    budget = settings.get("monthlyBudget")
    if budget:
        budget = float(budget)
        if this_total > budget:
            reminders.append({
                "level": "warn",
                "title": "超预算",
                "text": "本月已支出 %s，超过预算 %s，超出 %s" % (_fmt(this_total), _fmt(budget), _fmt(this_total - budget)),
            })
        elif this_total > budget * 0.8:
            reminders.append({
                "level": "info",
                "title": "预算提醒",
                "text": "本月已支出 %s，已使用预算的 %d%%" % (_fmt(this_total), round(this_total / budget * 100)),
            })

    if this_total > 0:
        grouped = {}
        for t in this_expenses:
            name = t.get("category") or "未分类"
            grouped[name] = grouped.get(name, 0) + t["amount"]
        top_name, top_amount = max(grouped.items(), key=lambda kv: kv[1])
        if top_amount / this_total >= 0.5:
            reminders.append({
                "level": "info",
                "title": "支出集中",
                "text": "本月「%s」占支出的 %.0f%%，可留意是否过于集中" % (top_name, top_amount / this_total * 100),
            })

    prev_total = sum(t["amount"] for t in expenses if str(t.get("date", "")).startswith(prev_month))
    if prev_total > 0 and this_total > prev_total * 1.2:
        reminders.append({
            "level": "info",
            "title": "支出上升",
            "text": "本月支出较上月增长 %.0f%%，可关注变化" % ((this_total - prev_total) / prev_total * 100),
        })

    if not reminders:
        reminders.append({
            "level": "info",
            "title": "暂无提醒",
            "text": "导入账单后，这里会提示大额支出、超预算等情况。",
        })
    return reminders
