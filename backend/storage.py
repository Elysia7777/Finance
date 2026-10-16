#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local-only SQLite persistence for one person on this host."""

import json
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock

from dedup import dedup

DB_PATH = os.environ.get(
    "PFA_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance.db"),
)
DEFAULT_PROFILE = {"displayName": "我的空间", "avatar": "🌱"}
DEFAULT_SETTINGS = {
    "theme": "light",
    "largeThreshold": 1000,
    "rules": [],
    "platforms": {"wechat": True, "alipay": False, "unionpay": False},
}
_DAILY_CATEGORIES = (
    ("餐饮", ("餐", "咖啡", "奶茶", "食品", "外卖", "食堂", "超市")),
    ("交通", ("地铁", "公交", "打车", "滴滴", "铁路", "加油", "停车")),
    ("医疗", ("医院", "药", "医疗", "诊所")),
    ("购物", ("淘宝", "京东", "拼多多", "商场", "购物")),
    ("娱乐", ("电影", "游戏", "娱乐", "演出")),
)
_MONTHLY_KEYWORDS = ("房租", "房贷", "水电", "电费", "水费", "燃气", "物业",
                     "话费", "宽带", "月费", "订阅", "保险", "贷款")
_IMPORT_LOCK = Lock()


@contextmanager
def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    database_dir = os.path.dirname(DB_PATH)
    if database_dir:
        os.makedirs(database_dir, exist_ok=True)
    with _connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                direction TEXT NOT NULL,
                category TEXT NOT NULL,
                bucket TEXT NOT NULL,
                merchant TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                raw TEXT NOT NULL DEFAULT '{}',
                category_overridden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS transactions_date_idx ON transactions(date);
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_price REAL NOT NULL,
                current_price REAL NOT NULL,
                currency TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_data (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        transaction_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(transactions)")
        }
        if "category_overridden" not in transaction_columns:
            connection.execute(
                "ALTER TABLE transactions ADD COLUMN category_overridden INTEGER NOT NULL DEFAULT 0"
            )


def _get_json(key, default):
    with _connect() as connection:
        row = connection.execute("SELECT value FROM app_data WHERE key = ?", (key,)).fetchone()
    if not row:
        return dict(default)
    return json.loads(row["value"])


def _set_json(key, value):
    encoded = json.dumps(value, ensure_ascii=False)
    with _connect() as connection:
        connection.execute(
            "INSERT INTO app_data(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, encoded),
        )


def get_profile():
    profile = dict(DEFAULT_PROFILE)
    profile.update(_get_json("profile", {}))
    return profile


def save_profile(profile):
    name = str(profile.get("displayName") or "").strip()
    avatar = str(profile.get("avatar") or "").strip()
    if not name or len(name) > 40:
        raise ValueError("昵称需为 1-40 个字符")
    if not avatar or len(avatar) > 16:
        raise ValueError("请选择有效的头像")
    value = {"displayName": name, "avatar": avatar}
    _set_json("profile", value)
    return value


def get_settings():
    settings = dict(DEFAULT_SETTINGS)
    settings.update(_get_json("settings", {}))
    settings["platforms"] = dict(DEFAULT_SETTINGS["platforms"], **settings.get("platforms", {}))
    return settings


def save_settings(changes):
    settings = get_settings()
    if "theme" in changes:
        if changes["theme"] not in ("light", "dark", "lavender", "mint"):
            raise ValueError("不支持的主题")
        settings["theme"] = changes["theme"]
    if "largeThreshold" in changes:
        try:
            threshold = float(changes["largeThreshold"])
        except (TypeError, ValueError) as exc:
            raise ValueError("大型开支门槛必须是数字") from exc
        if not 0 < threshold <= 100000000:
            raise ValueError("大型开支门槛需大于 0")
        settings["largeThreshold"] = threshold
    if "rules" in changes:
        rules = changes["rules"]
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError("分类规则格式无效")
        checked = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("分类规则格式无效")
            keyword = str(rule.get("keyword") or "").strip()
            category = str(rule.get("category") or "").strip()
            bucket = rule.get("bucket", "monthly")
            if not keyword or len(keyword) > 100 or not category or len(category) > 40:
                raise ValueError("规则关键词或分类不能为空，且需在长度限制内")
            if bucket not in ("daily", "monthly", "large"):
                raise ValueError("规则类型无效")
            amount = rule.get("amount")
            if amount not in (None, ""):
                try:
                    amount = round(float(amount), 2)
                except (TypeError, ValueError) as exc:
                    raise ValueError("规则金额必须是数字") from exc
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("规则金额不能小于 0")
            else:
                amount = None
            checked.append({
                "id": str(rule.get("id") or uuid.uuid4().hex),
                "keyword": keyword,
                "amount": amount,
                "category": category,
                "bucket": bucket,
            })
        settings["rules"] = checked
    _set_json("settings", settings)
    if "rules" in changes or "largeThreshold" in changes:
        _reclassify_all(settings)
    return settings


def _classify(record, settings):
    text = " ".join((record.get("merchant", ""), record.get("note", ""),
                     record.get("category", ""), record.get("raw", "")))
    amount = round(float(record["amount"]), 2)
    if record["direction"] == "income":
        return "收入", "income"
    for rule in settings.get("rules", []):
        if rule["keyword"].casefold() in text.casefold() and (
            rule.get("amount") is None or round(rule["amount"], 2) == amount
        ):
            return rule["category"], rule["bucket"]
    if amount >= float(settings.get("largeThreshold", 1000)):
        return "大型开支", "large"
    if any(keyword in text for keyword in _MONTHLY_KEYWORDS):
        return "月度开支", "monthly"
    for category, keywords in _DAILY_CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return category, "daily"
    return "日常开支", "daily"


def _record_from_row(row):
    return {
        "id": row["id"], "date": row["date"], "amount": row["amount"],
        "direction": row["direction"], "category": row["category"],
        "bucket": row["bucket"], "merchant": row["merchant"], "note": row["note"],
        "source": row["source"],
    }


def _reclassify_all(settings):
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, direction, amount, merchant, note, category, raw "
            "FROM transactions WHERE category_overridden = 0"
        ).fetchall()
        for row in rows:
            category, bucket = _classify(dict(row), settings)
            connection.execute(
                "UPDATE transactions SET category = ?, bucket = ? WHERE id = ?",
                (category, bucket, row["id"]),
            )


def save_transactions(records):
    init_db()
    with _IMPORT_LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = [dict(row) for row in connection.execute(
            "SELECT fingerprint FROM transactions"
        ).fetchall()]
        additions, duplicates = dedup(records, existing)
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for record in additions:
            category, bucket = _classify(record, settings)
            connection.execute(
                """INSERT INTO transactions
                (id, fingerprint, date, amount, direction, category, bucket,
                 merchant, note, source, raw, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, record["fingerprint"], record["date"],
                 record["amount"], record["direction"], category, bucket,
                 record.get("merchant", ""), record.get("note", ""),
                 record.get("source", ""), record.get("raw", "{}"), now),
            )
    return len(additions), len(duplicates)


def load_transactions(month=None):
    init_db()
    query = "SELECT * FROM transactions"
    args = ()
    if month:
        query += " WHERE date LIKE ?"
        args = (str(month) + "%",)
    query += " ORDER BY date DESC, created_at DESC"
    with _connect() as connection:
        return [_record_from_row(row) for row in connection.execute(query, args).fetchall()]


def update_transaction(transaction_id, changes):
    category = str(changes.get("category") or "").strip()
    if not category or len(category) > 40:
        raise ValueError("分类需为 1-40 个字符")
    with _connect() as connection:
        cursor = connection.execute(
            "UPDATE transactions SET category = ?, category_overridden = 1 WHERE id = ?",
            (category, transaction_id),
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return _record_from_row(row)


def load_items():
    init_db()
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return [{
        "id": row["id"], "name": row["name"], "type": row["type"],
        "quantity": row["quantity"], "costPrice": row["cost_price"],
        "currentPrice": row["current_price"], "value": row["quantity"] * row["current_price"],
        "currency": row["currency"], "note": row["note"],
    } for row in rows]


def save_item(item):
    name = str(item.get("name") or "").strip()
    kind = item.get("type")
    if not name or len(name) > 80 or kind not in ("fund", "stock", "cash", "other"):
        raise ValueError("请提供有效的名称和资产类型")
    try:
        quantity = float(item.get("quantity", 1))
        cost_price = float(item.get("costPrice", 0))
        current_price = float(item.get("currentPrice", cost_price))
    except (TypeError, ValueError) as exc:
        raise ValueError("数量、成本价和当前价格必须是数字") from exc
    if any(not math.isfinite(value) or value < 0
           for value in (quantity, cost_price, current_price)):
        raise ValueError("资产数值必须为有限的非负数")
    value = quantity * current_price
    if not math.isfinite(value):
        raise ValueError("资产估值超出支持范围")
    item_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            """INSERT INTO items
            (id, name, type, quantity, cost_price, current_price, currency, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, name, kind, quantity, cost_price, current_price,
             str(item.get("currency") or "CNY")[:8],
             str(item.get("note") or "")[:200],
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    return next(entry for entry in load_items() if entry["id"] == item_id)


def delete_item(item_id):
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return cursor.rowcount > 0
