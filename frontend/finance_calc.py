#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户端金融计算逻辑框架（占位）
─────────────────────────────
架构约定：所有金融计算（估值、汇率换算、收益统计等）都在客户端执行，
服务器端不承担任何计算。以下函数签名即团队开发契约 —— 参数与返回值
结构供界面调用，函数体全部待补充（TODO）。

数据来源约定：
- 项目列表：GET /api/items（字段见 backend/server.py 中 stub_items）
- 行情/汇率：GET /api/panels（字段见 backend/panels.json）
"""


def calc_portfolio_value(items, fx_rates, base="CNY"):
    """计算项目组合总值（按基础货币折算）。

    :param items: 项目列表，每项如 {"id": "...", "name": "...", "type": "cash",
                  "value": 10000, "currency": "CNY"}
    :param fx_rates: 汇率表（相对基础货币，如 {"USD": 7.1}）
    :param base: 基础货币代码，默认 "CNY"
    :return: 折算后的组合总值（float）
    """
    # TODO(团队): 实现组合估值（明确缺失币种的处理策略）
    raise NotImplementedError("calc_portfolio_value 待实现（金融计算逻辑框架占位）")


def convert(amount, from_ccy, to_ccy, fx_rates):
    """汇率换算。

    :param amount: 金额
    :param from_ccy: 源币种代码
    :param to_ccy: 目标币种代码
    :param fx_rates: 汇率表（相对基础货币）
    :return: 折算后的金额（float）
    """
    # TODO(团队): 实现汇率换算（注意除零与缺失币种的处理）
    raise NotImplementedError("convert 待实现（金融计算逻辑框架占位）")


def calc_item_return(item, market_data):
    """单项目收益统计。

    :param item: 项目条目（结构同 GET /api/items 中的条目）
    :param market_data: 对应板块的行情数据（结构与 GET /api/panels 一致）
    :return: 收益结果，如 {"changePct": 2.5, "changeValue": 625.0}
    """
    # TODO(团队): 实现收益统计（依赖行情接入后的真实数据结构）
    raise NotImplementedError("calc_item_return 待实现（金融计算逻辑框架占位）")


def _expect_stub(name, fn):
    """契约自测：占位函数应按约定抛出 NotImplementedError。"""
    try:
        fn()
    except NotImplementedError:
        print(f"[契约] {name} -> 待实现 [OK]")
    else:
        raise AssertionError(f"{name} 应保持占位状态（抛出 NotImplementedError）")


if __name__ == "__main__":
    # 契约自测（CI 会运行本文件）
    _expect_stub("calc_portfolio_value", lambda: calc_portfolio_value([], {}, "CNY"))
    _expect_stub("convert", lambda: convert(1.0, "USD", "CNY", {}))
    _expect_stub("calc_item_return", lambda: calc_item_return({}, {}))
    print("finance_calc 框架自测通过：3 个计算契约均为占位状态（待团队实现）")
