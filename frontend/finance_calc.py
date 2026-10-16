#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Client-side calculations for asset values and currency conversion."""

import math


def _finite_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 必须是数字" % name) from exc
    if not math.isfinite(number):
        raise ValueError("%s 必须是有限数字" % name)
    return number


def _to_base(amount, currency, fx_rates, base):
    if currency == base:
        return amount
    rate = _finite_number(fx_rates.get(currency), "缺少币种汇率：" + currency)
    if rate <= 0:
        raise ValueError("币种汇率必须大于 0：" + currency)
    return amount * rate


def calc_portfolio_value(items, fx_rates, base="CNY"):
    """Sum item market values after converting each currency to the base."""
    total = 0.0
    for item in items:
        if "currentPrice" in item and "quantity" in item:
            value = _finite_number(item["currentPrice"], "当前价格") * _finite_number(item["quantity"], "持有数量")
        else:
            value = _finite_number(item.get("value"), "资产价值")
        currency = str(item.get("currency") or base).upper()
        total += _to_base(value, currency, fx_rates, base.upper())
    return total


def convert(amount, from_ccy, to_ccy, fx_rates):
    """Convert currencies using rates expressed as units of base per currency."""
    amount = _finite_number(amount, "金额")
    from_ccy = str(from_ccy).upper()
    to_ccy = str(to_ccy).upper()
    if from_ccy == to_ccy:
        return amount
    from_rate = _to_base(1.0, from_ccy, fx_rates, "CNY")
    to_rate = _to_base(1.0, to_ccy, fx_rates, "CNY")
    return amount * from_rate / to_rate


def calc_item_return(item, market_data=None):
    """Return current market value and unrealized gain for a held asset."""
    quantity = _finite_number(item.get("quantity", 1), "持有数量")
    current_price = _finite_number(item.get("currentPrice", item.get("value", 0)), "当前价格")
    cost_price = _finite_number(item.get("costPrice", 0), "成本价格")
    value = quantity * current_price
    cost = quantity * cost_price
    change = value - cost
    change_pct = change / cost * 100 if cost else None
    return {"value": value, "costValue": cost, "changeValue": change, "changePct": change_pct}


def _self_test():
    assert calc_portfolio_value(
        [{"quantity": 2, "currentPrice": 10, "currency": "CNY"},
         {"value": 5, "currency": "USD"}],
        {"USD": 7.1},
    ) == 55.5
    assert round(convert(10, "USD", "CNY", {"USD": 7.1}), 2) == 71.0
    assert round(convert(71, "CNY", "USD", {"USD": 7.1}), 2) == 10.0
    assert calc_item_return({"quantity": 2, "costPrice": 5, "currentPrice": 7}) == {
        "value": 14.0, "costValue": 10.0, "changeValue": 4.0, "changePct": 40.0,
    }
    try:
        convert(1, "XYZ", "CNY", {})
    except ValueError:
        pass
    else:
        raise AssertionError("缺少汇率应明确报错")
    print("finance_calc 自测通过")


if __name__ == "__main__":
    _self_test()
