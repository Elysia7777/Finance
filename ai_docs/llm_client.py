#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【C · AI、测试、文档】LLM 集成（框架占位，🚧 待开发）
─────────────────────────────────────────────────
职责：为主界面「问答」「提醒」板块提供大模型能力。
TODO(C组): 选型模型与 SDK、设计 Prompt、封装调用与异常处理；
          测试计划执行与文档维护（README / PPT/）也由 C 组负责。
"""


def ask(question, context):
    """流水问答：结合用户流水数据回答自然语言问题。

    :param question: 用户问题，如 "我这个月餐饮花了多少？"
    :param context: 流水记录列表（结构见 backend/bill_parser.py 契约）
    :return: 回答文本
    """
    raise NotImplementedError("ask 待实现（C·LLM 集成占位）")


def build_reminders(transactions):
    """消费提醒：基于近期流水生成提醒列表。

    :param transactions: 流水记录列表
    :return: 提醒列表，如 [{"level": "info|warn", "text": "..."}]
    """
    raise NotImplementedError("build_reminders 待实现（C·LLM 集成占位）")
