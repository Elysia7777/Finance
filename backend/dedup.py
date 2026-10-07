#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【B · 后端与数据】去重校验（框架占位，🚧 待开发）
─────────────────────────────────────────────────
职责：对导入的流水做去重校验，避免同一笔交易重复入库。
TODO(B组): 定义重复判定键（如 日期+金额+摘要）与冲突处理策略。
"""


def dedup(records, existing=None):
    """流水去重校验。

    :param records: 本次导入的流水列表
    :param existing: 库中已有流水列表（可为 None）
    :return: (去重后新增的记录列表, 重复/冲突的记录列表)
    """
    raise NotImplementedError("dedup 待实现（B·去重校验占位）")
