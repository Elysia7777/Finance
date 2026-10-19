#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接入大模型的个人财务问答，未配置或调用失败时回退到本地规则问答。

本模块不引入第三方依赖，使用标准库 urllib 调用兼容 OpenAI 的
``/chat/completions`` 接口，因此 OpenAI、DeepSeek、Moonshot、Ollama 等
服务只要提供兼容接口都能使用。

通过环境变量配置：
    LLM_API_KEY     API 密钥（必填，不配置则使用本地规则问答）
    LLM_BASE_URL    接口地址，默认 https://api.openai.com/v1
    LLM_MODEL       模型名称，默认 gpt-4o-mini
    LLM_TIMEOUT     请求超时秒数，默认 20

也可以在网页端「助手 → 大模型」面板中配置，页面配置优先于环境变量；
页面未启用时仍可使用环境变量方式启动。

设计原则：金额与汇总始终以 backend/qa.py 的确定性计算结果为准，
大模型只负责把核算结果组织成自然语言、并结合账单回答开放性提问；
这样即使模型“胡编”也不会污染数字。
"""

import json
import os
import sys
import urllib.error
import urllib.request

_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import qa  # noqa: E402


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value and value.strip() else default


def _env_config():
    return {
        "apiKey": _env("LLM_API_KEY"),
        "baseUrl": (_env("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        "model": _env("LLM_MODEL") or "gpt-4o-mini",
        "timeout": float(_env("LLM_TIMEOUT") or 20),
    }


def _resolve_config(llm_config=None):
    """页面配置优先；页面未启用时回退环境变量，便于命令行启动的场景。"""
    env = _env_config()
    page = llm_config or {}
    if page.get("enabled"):
        return {
            "apiKey": str(page.get("apiKey") or "").strip() or env["apiKey"],
            "baseUrl": (str(page.get("baseUrl") or "").strip() or env["baseUrl"]).rstrip("/"),
            "model": str(page.get("model") or "").strip() or env["model"],
            "timeout": float(page.get("timeout") or env["timeout"]),
        }
    return env


def is_configured(llm_config=None):
    return bool(_resolve_config(llm_config)["apiKey"])


def status(llm_config=None):
    config = _resolve_config(llm_config)
    return {
        "configured": bool(config["apiKey"]),
        "enabled": bool((llm_config or {}).get("enabled")),
        "hasApiKey": bool(config["apiKey"]),
        "model": config["model"],
        "baseUrl": config["baseUrl"],
    }


def _fmt(value):
    return "¥%s" % format(round(float(value or 0), 2), ",.2f")


def _summarize(transactions):
    """把流水压成一段可供大模型阅读的文本摘要，避免超长输入。"""
    transactions = transactions or []
    income_rows = [t for t in transactions if t.get("direction") == "income"]
    expense_rows = [t for t in transactions if t.get("direction") == "expense"]

    category_totals = {}
    for t in expense_rows:
        name = t.get("category") or "未分类"
        category_totals[name] = category_totals.get(name, 0) + float(t.get("amount") or 0)

    parts = [
        "共 %d 笔流水，其中支出 %d 笔、收入 %d 笔。" % (
            len(transactions), len(expense_rows), len(income_rows)),
        "支出分类汇总：%s。" % (
            "、".join("%s %s" % (name, _fmt(total))
                      for name, total in sorted(
                          category_totals.items(), key=lambda kv: -kv[1])) or "暂无"),
    ]
    recent = sorted(transactions, key=lambda t: str(t.get("date", "")), reverse=True)[:100]
    lines = ["最近流水："]
    for t in recent:
        direction = "+" if t.get("direction") == "income" else "-"
        label = t.get("merchant") or t.get("category") or "未命名"
        note = t.get("note") or ""
        detail = "%s %s" % (label, note) if note else label
        lines.append(
            "%s %s %s%s  %s" % (
                t.get("date", ""), direction, _fmt(t.get("amount")), "", detail)
        )
    parts.append("\n".join(lines))
    return "\n".join(parts)


def _call_chat(messages, config):
    if not config["apiKey"]:
        raise RuntimeError("未配置 API Key")
    endpoint = config["baseUrl"]
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + config["apiKey"],
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
        body = json.loads(response.read().decode("utf-8"))
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("模型返回格式无法解析") from exc
    return str(content).strip()


def _messages(question, baseline, transactions, history=None):
    system = (
        "你是「小账本」的个人财务助手。只依据用户提供的账单摘要与核算结果回答，"
        "不得编造流水或数字。金额采用人民币，保留两位小数。"
        "核算结果中的数字是唯一权威来源，回答时不得改动。"
        "对于账单范围之外的信息（如实时行情、没有记录的消费），请明确说明无法从本地账单得知。"
        "使用简体中文，回答简洁、直接。"
    )
    user = (
        "用户问题：%s\n\n"
        "【账单摘要】\n%s\n\n"
        "【本地核算结果（金额以此为准）】\n%s\n\n"
        "请基于以上信息回答用户问题；可以适当给出简明、有依据的理财建议，但不要编造数据。"
    ) % (baseline.get("question", ""), _summarize(transactions), baseline["answer"])
    messages = [
        {"role": "system", "content": system},
    ]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user})
    return messages


def ask(question, transactions, settings=None, llm_config=None, history=None):
    """回答自然语言问题；配置了 LLM 时优先走大模型，否则回退规则问答。

    返回结构与 qa.answer 兼容，并额外携带 ``source``、``model`` 等字段，
    前端可以直接渲染 ``answer`` 与 ``evidence``。
    """
    question = (question or "").strip()
    baseline = qa.answer(question, transactions, settings)
    config = _resolve_config(llm_config)

    if not config["apiKey"]:
        baseline["source"] = "local"
        baseline["fallbackReason"] = "未配置 API Key"
        return baseline

    try:
        answer = _call_chat(
            _messages(question, baseline, transactions, history), config
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            RuntimeError, ValueError, KeyError) as exc:
        baseline["source"] = "local"
        baseline["fallbackReason"] = "LLM 调用失败，已回退本地核算：%s" % exc
        return baseline

    return {
        "question": question,
        "answer": answer,
        "metric": baseline.get("metric"),
        "period": baseline.get("period"),
        "periodLabel": baseline.get("periodLabel"),
        "category": baseline.get("category"),
        "evidence": baseline.get("evidence", []),
        "source": "llm",
        "model": config["model"],
        "fallbackAnswer": baseline.get("answer"),
    }


def build_reminders(transactions):
    """消费提醒沿用确定性实现，保证提醒数字准确。"""
    return qa.build_reminders(transactions)
