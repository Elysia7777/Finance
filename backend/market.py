#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共行情源抓取：股票 / 汇率 / 贵金属。

数据仅供课程展示，不用于交易。所有行情均来自公开接口并标注来源：
    - 股票：腾讯行情 qt.gtimg.cn
    - 汇率：新浪财经 hq.sinajs.cn（日元按美元兑日元换算）
    - 贵金属：新浪财经·上海黄金交易所（延期行情）

抓取结果缓存 60 秒；任一来源失败时回退到 panels.json 占位数据，
并在每一行标注“行情源暂不可用”，保证页面始终可用。
"""

import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANELS_FILE = os.path.join(BASE_DIR, "panels.json")
SINA_URL = "http://hq.sinajs.cn/list="
SINA_REFERER = "https://finance.sina.com.cn"
TENCENT_URL = "http://qt.gtimg.cn/q="
CACHE_SECONDS = 60
TIMEOUT = 6

_STOCK_CODES = {"sh600519": "600519", "hk00700": "00700", "usAAPL": "AAPL"}

_lock = threading.Lock()
_cache = {"time": 0.0, "panels": None}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _http(url, referer=None):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if referer:
        request.add_header("Referer", referer)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=TIMEOUT) as response:
        return response.read().decode("gb18030", "replace")


def _fetch_stocks():
    text = _http(TENCENT_URL + ",".join(_STOCK_CODES))
    result = {}
    for key, body in re.findall(r'v_([A-Za-z0-9]+)="([^"]*)"', text):
        parts = body.split("~")
        if len(parts) <= 32:
            continue
        code = _STOCK_CODES.get(key)
        if not code:
            continue
        result[code] = {
            "name": parts[1],
            "price": _num(parts[3]),
            "changePct": _num(parts[32]),
        }
    return result


def _fetch_fx():
    text = _http(SINA_URL + "USDCNY,EURCNY,HKDCNY,USDJPY", referer=SINA_REFERER)
    values = {}
    for code, body in re.findall(r'hq_str_([A-Z]+)="([^"]*)"', text):
        parts = body.strip().split(",")
        if len(parts) >= 2:
            values[code] = _num(parts[1])
    rates = {}
    for code in ("USDCNY", "EURCNY", "HKDCNY"):
        if values.get(code) is not None:
            rates[code] = values[code]
    if values.get("USDCNY") and values.get("USDJPY"):
        rates["JPYCNY"] = round(values["USDCNY"] / values["USDJPY"], 6)
    return rates


def _fetch_metals():
    text = _http(SINA_URL + "gds_AUTD,gds_AGTD,gds_PT9995", referer=SINA_REFERER)
    values = {}
    for code, body in re.findall(r'hq_str_([A-Za-z0-9_]+)="([^"]*)"', text):
        parts = body.split(",")
        if parts and parts[0].strip():
            values[code] = _num(parts[0])
    result = {}
    if values.get("gds_AUTD") is not None:
        result["XAU"] = {"price": values["gds_AUTD"], "unit": "元/克"}
    if values.get("gds_AGTD") is not None:
        result["XAG"] = {"price": round(values["gds_AGTD"] / 1000, 3), "unit": "元/克"}
    if values.get("gds_PT9995") is not None:
        result["PT"] = {"price": values["gds_PT9995"], "unit": "元/克"}
    return result


def _build_panels():
    with open(PANELS_FILE, "r", encoding="utf-8") as file:
        panels = json.load(file)

    errors = {}
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            "stocks": pool.submit(_fetch_stocks),
            "fx": pool.submit(_fetch_fx),
            "metals": pool.submit(_fetch_metals),
        }
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - 任何失败都回退占位
                errors[name] = str(exc)
                results[name] = {}

    for row in panels.get("stocks", {}).get("rows", []):
        data = results.get("stocks", {}).get(row.get("code"))
        if data and data.get("price") is not None:
            row["price"] = data["price"]
            row["changePct"] = data["changePct"]
            row["source"] = "腾讯行情"
        else:
            row["price"] = None
            row["changePct"] = None
            row["source"] = "行情源暂不可用" if "stocks" in errors else "待接入"

    for row in panels.get("currencies", {}).get("rows", []):
        rate = results.get("fx", {}).get(str(row.get("code")) + "CNY")
        if rate is not None:
            row["rate"] = rate
            row["source"] = "新浪财经"
        else:
            row["rate"] = None
            row["source"] = "行情源暂不可用" if "fx" in errors else "待接入"

    for row in panels.get("metals", {}).get("rows", []):
        data = results.get("metals", {}).get(row.get("code"))
        if data and data.get("price") is not None:
            row["price"] = data["price"]
            row["source"] = "新浪财经·上海金交所"
        else:
            row["price"] = None
            row["source"] = "行情源暂不可用" if "metals" in errors else "待接入"

    panels["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    panels["disclaimer"] = "行情来自公开数据源，仅供课程展示，不用于交易。"
    return panels


def get_panels():
    with _lock:
        now = time.time()
        if _cache["panels"] is not None and now - _cache["time"] < CACHE_SECONDS:
            return _cache["panels"]
    try:
        panels = _build_panels()
    except Exception:  # noqa: BLE001 - 兜底返回占位文件
        with open(PANELS_FILE, "r", encoding="utf-8") as file:
            panels = json.load(file)
        panels["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _cache["panels"] = panels
        _cache["time"] = time.time()
    return panels
