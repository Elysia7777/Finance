#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【B · 后端与数据】SQLite 存储（框架占位，🚧 待开发）
─────────────────────────────────────────────────
职责：用 SQLite（标准库 sqlite3）持久化流水、账号等业务数据。
说明：当前账号数据仍由 server.py 以 JSON 文件（backend/db.json）极简存储；
     B 组实现本模块后可逐步迁移。数据库文件约定：backend/finance.db（已 gitignore）。
TODO(B组): 设计表结构（如 users / transactions），实现初始化与读写。
"""

import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance.db")


def init_db():
    """建库建表。TODO(B组): 设计表结构后实现。"""
    raise NotImplementedError("init_db 待实现（B·SQLite 存储占位）")


def save_transactions(records):
    """批量写入流水记录。TODO(B组)"""
    raise NotImplementedError("save_transactions 待实现（B·SQLite 存储占位）")


def load_transactions(user_id=None):
    """读取流水记录（可按账号过滤）。TODO(B组)"""
    raise NotImplementedError("load_transactions 待实现（B·SQLite 存储占位）")
