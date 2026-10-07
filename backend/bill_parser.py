#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【B · 后端与数据】账单解析（框架占位，🚧 待开发）
─────────────────────────────────────────────────
职责：把导入的账单文件（CSV / Excel 等）解析为统一的流水记录列表。
TODO(B组): 确定支持的账单格式与统一流水字段，并实现解析。
"""

# 统一流水记录结构（契约草案，待 B 组确认后固化）：
# {"date": "YYYY-MM-DD", "amount": -25.0, "currency": "CNY",
#  "category": "餐饮", "note": "摘要", "source": "微信账单"}


def parse_bill(file_bytes, filename):
    """解析一份账单文件。

    :param file_bytes: 账单文件内容（bytes）
    :param filename: 文件名（用于识别格式与来源）
    :return: 统一流水记录列表（结构见上方契约草案）
    """
    raise NotImplementedError("parse_bill 待实现（B·账单解析占位）")
