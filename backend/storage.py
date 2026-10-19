#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local-only SQLite persistence for one person on this host."""

import json
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
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
    "monthlyBudget": None,
    "rules": [],
    "platforms": {"wechat": True, "alipay": False, "unionpay": False},
}
DEFAULT_LLM_CONFIG = {
    "enabled": False,
    "apiKey": "",
    "baseUrl": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "timeout": 20,
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
                code TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL,
                cost_price REAL NOT NULL,
                current_price REAL NOT NULL,
                currency TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'local',
                model TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_idx
                ON messages(conversation_id, created_at);
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
        item_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(items)")
        }
        if "code" not in item_columns:
            connection.execute(
                "ALTER TABLE items ADD COLUMN code TEXT NOT NULL DEFAULT ''"
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
    if "monthlyBudget" in changes:
        value = changes["monthlyBudget"]
        if value in (None, ""):
            settings["monthlyBudget"] = None
        else:
            try:
                budget = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("每月预算必须是数字") from exc
            if not 0 < budget <= 100000000:
                raise ValueError("每月预算需大于 0")
            settings["monthlyBudget"] = round(budget, 2)
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


def get_llm_config():
    config = dict(DEFAULT_LLM_CONFIG)
    stored = _get_json("llm_config", {})
    for key in DEFAULT_LLM_CONFIG:
        if key in stored:
            config[key] = stored[key]
    return config


def get_llm_public_config():
    config = get_llm_config()
    return {
        "enabled": bool(config["enabled"]),
        "hasApiKey": bool(str(config.get("apiKey") or "").strip()),
        "baseUrl": str(config["baseUrl"]),
        "model": str(config["model"]),
        "timeout": config["timeout"],
    }


def save_llm_config(changes):
    changes = changes or {}
    config = get_llm_config()
    if "enabled" in changes:
        config["enabled"] = bool(changes["enabled"])
    if changes.get("clearApiKey"):
        config["apiKey"] = ""
    if str(changes.get("apiKey") or "").strip():
        config["apiKey"] = str(changes["apiKey"]).strip()
    if "baseUrl" in changes:
        base_url = str(changes.get("baseUrl") or "").strip()
        if base_url and not base_url.lower().startswith(("http://", "https://")):
            raise ValueError("接口地址需以 http:// 或 https:// 开头")
        config["baseUrl"] = base_url
    if "model" in changes:
        model = str(changes.get("model") or "").strip()
        if not model or len(model) > 100:
            raise ValueError("模型名称需为 1-100 个字符")
        config["model"] = model
    if "timeout" in changes:
        try:
            timeout = float(changes["timeout"])
        except (TypeError, ValueError) as exc:
            raise ValueError("超时时间必须是数字") from exc
        if not 1 <= timeout <= 300:
            raise ValueError("超时时间需在 1-300 秒之间")
        config["timeout"] = int(timeout)
    _set_json("llm_config", config)
    return config


def _conversation_dict(row, message_count):
    return {
        "id": row["id"],
        "title": row["title"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "messageCount": message_count,
    }


def create_conversation():
    init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conversation_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, "新对话", now, now),
        )
    return _conversation_dict(
        {"id": conversation_id, "title": "新对话",
         "created_at": now, "updated_at": now}, 0)


def list_conversations():
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT c.*, COUNT(m.id) AS message_count "
            "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
            "GROUP BY c.id ORDER BY c.updated_at DESC, c.created_at DESC"
        ).fetchall()
    return [_conversation_dict(row, row["message_count"]) for row in rows]


def get_conversation_messages(conversation_id):
    init_db()
    with _connect() as connection:
        row = connection.execute(
            "SELECT c.*, COUNT(m.id) AS message_count "
            "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
            "WHERE c.id = ? GROUP BY c.id",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        message_rows = connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (conversation_id,),
        ).fetchall()
    messages = [{
        "id": message["id"],
        "role": message["role"],
        "content": message["content"],
        "source": message["source"],
        "model": message["model"],
        "evidence": json.loads(message["evidence"] or "[]"),
        "createdAt": message["created_at"],
    } for message in message_rows]
    return {"conversation": _conversation_dict(row, row["message_count"]), "messages": messages}


def add_message(conversation_id, role, content, source="local", model="", evidence=None):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    message_id = uuid.uuid4().hex
    with _connect() as connection:
        exists = connection.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not exists:
            return None
        connection.execute(
            "INSERT INTO messages"
            "(id, conversation_id, role, content, source, model, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, source, model,
             json.dumps(evidence or [], ensure_ascii=False), now),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        if role == "user":
            title = content if len(content) <= 24 else content[:24] + "…"
            connection.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = '新对话'",
                (title, conversation_id),
            )
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "source": source,
        "model": model,
        "evidence": evidence or [],
        "createdAt": now,
    }


def delete_conversation(conversation_id):
    init_db()
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
    return cursor.rowcount > 0


def _classify(record, settings):
    text = " ".join((record.get("merchant", ""), record.get("note", ""),
                     record.get("category", ""), record.get("raw", "")))
    amount = round(float(record["amount"]), 2)
    if record["direction"] == "neutral":
        return "不计收支", "neutral"
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


def load_transactions(month=None, keyword=None):
    init_db()
    query = "SELECT * FROM transactions"
    clauses = []
    args = []
    if month:
        clauses.append("date LIKE ?")
        args.append(str(month) + "%")
    if keyword:
        clauses.append(
            "(merchant LIKE ? OR note LIKE ? OR category LIKE ? OR source LIKE ?)"
        )
        like = "%" + str(keyword) + "%"
        args.extend((like, like, like, like))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY date DESC, created_at DESC"
    with _connect() as connection:
        return [_record_from_row(row) for row in connection.execute(query, args).fetchall()]


def update_transaction(transaction_id, changes):
    changes = changes or {}
    updates = {}
    if "category" in changes:
        category = str(changes.get("category") or "").strip()
        if not category or len(category) > 40:
            raise ValueError("分类需为 1-40 个字符")
        updates["category"] = category
        updates["category_overridden"] = 1
    if "amount" in changes:
        try:
            amount = float(changes.get("amount"))
        except (TypeError, ValueError) as exc:
            raise ValueError("金额必须是数字") from exc
        if not math.isfinite(amount) or amount <= 0 or amount > 100000000:
            raise ValueError("金额需为大于 0 的有效数字")
        updates["amount"] = round(amount, 2)
    if "date" in changes:
        text = str(changes.get("date") or "").strip()
        try:
            updates["date"] = date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError("日期需为 YYYY-MM-DD 格式") from exc
    if "direction" in changes:
        direction = str(changes.get("direction") or "").strip()
        if direction not in ("income", "expense"):
            raise ValueError("收支类型无效")
        updates["direction"] = direction
    if "merchant" in changes:
        merchant = str(changes.get("merchant") or "").strip()
        if len(merchant) > 200:
            raise ValueError("交易对象需在 200 字以内")
        updates["merchant"] = merchant
    if "note" in changes:
        note = str(changes.get("note") or "").strip()
        if len(note) > 500:
            raise ValueError("备注需在 500 字以内")
        updates["note"] = note

    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if not row:
            return None
        if "direction" in updates:
            if updates["direction"] == "income":
                updates["bucket"] = "income"
            elif updates["direction"] == "expense" and row["bucket"] == "income":
                updates["bucket"] = "daily"
        if not updates:
            raise ValueError("没有可更新的字段")
        set_clause = ", ".join("%s = ?" % key for key in updates)
        connection.execute(
            "UPDATE transactions SET %s WHERE id = ?" % set_clause,
            list(updates.values()) + [transaction_id],
        )
        row = connection.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
    return _record_from_row(row)


def delete_transaction(transaction_id):
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM transactions WHERE id = ?", (transaction_id,)
        )
    return cursor.rowcount > 0


def _item_dict(row):
    return {
        "id": row["id"], "name": row["name"], "type": row["type"],
        "code": row["code"], "quantity": row["quantity"],
        "costPrice": row["cost_price"], "currentPrice": row["current_price"],
        "value": row["quantity"] * row["current_price"],
        "currency": row["currency"], "note": row["note"],
    }


def _normalize_item(changes, existing=None):
    current = existing or {}
    name = str(changes.get("name", current.get("name")) or "").strip()
    kind = changes.get("type", current.get("type"))
    if not name or len(name) > 80 or kind not in ("fund", "stock", "cash", "other"):
        raise ValueError("请提供有效的名称和资产类型")
    code = str(changes.get("code", current.get("code")) or "").strip()
    if len(code) > 20:
        raise ValueError("代码不能超过 20 个字符")
    try:
        quantity = float(changes.get("quantity", current.get("quantity", 1)))
        cost_price = float(changes.get("costPrice", current.get("cost_price", 0)))
        current_price = float(changes.get("currentPrice", current.get("current_price", cost_price)))
    except (TypeError, ValueError) as exc:
        raise ValueError("数量、成本价和当前价格必须是数字") from exc
    if any(not math.isfinite(value) or value < 0
           for value in (quantity, cost_price, current_price)):
        raise ValueError("资产数值必须为有限的非负数")
    value = quantity * current_price
    if not math.isfinite(value):
        raise ValueError("资产估值超出支持范围")
    currency = str(changes.get("currency", current.get("currency") or "CNY"))[:8]
    note = str(changes.get("note", current.get("note") or ""))[:200]
    return name, kind, code, quantity, cost_price, current_price, currency, note


def load_items():
    init_db()
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return [_item_dict(row) for row in rows]


def save_item(item):
    name, kind, code, quantity, cost_price, current_price, currency, note = _normalize_item(item)
    item_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            """INSERT INTO items
            (id, name, type, code, quantity, cost_price, current_price, currency, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, name, kind, code, quantity, cost_price, current_price,
             currency, note,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    return next(entry for entry in load_items() if entry["id"] == item_id)


def update_item(item_id, changes):
    changes = changes or {}
    with _connect() as connection:
        row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        name, kind, code, quantity, cost_price, current_price, currency, note = _normalize_item(
            changes, dict(row)
        )
        connection.execute(
            "UPDATE items SET name = ?, type = ?, code = ?, quantity = ?, "
            "cost_price = ?, current_price = ?, currency = ?, note = ? WHERE id = ?",
            (name, kind, code, quantity, cost_price, current_price,
             currency, note, item_id),
        )
        row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _item_dict(row)


def delete_item(item_id):
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return cursor.rowcount > 0
