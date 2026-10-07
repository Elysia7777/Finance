#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Personal Finance Assistant —— 服务器端（单文件集成版，仅用 Python 标准库）

职责边界（团队开发约定）：
  1. 服务器端只负责：极简账号系统（注册/登录/登出）与账号信息管理；
  2. 金融计算逻辑一律放在客户端执行（见 frontend/finance_calc.py）；
  3. 各板块（股票/货币/贵金属/项目列表）业务功能为框架占位：返回桩数据或 501。

开发便利：本进程同时托管 frontend/ 静态页面；生产环境可将客户端单独部署。
"""
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8000
CLIENT_DIR_NAME = "frontend"  # 前端目录名（A·前端与体验；如需改名，改这里并同步 README）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "db.json")          # 运行时数据（已 gitignore，首次运行自动创建）
PANELS_FILE = os.path.join(BASE_DIR, "panels.json")  # 右侧板块占位数据
CLIENT_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", CLIENT_DIR_NAME))

_DB_LOCK = threading.Lock()
_TOKENS = {}  # token -> userId（内存会话；服务器重启后失效，客户端自动回退到未登录态）

NAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,32}$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# ---------- 存储与密码 ----------

def load_db():
    """读取账号数据（JSON 文件）。TODO(团队): 数据量增大时替换为 SQLite，保持函数签名不变。"""
    if not os.path.exists(DB_FILE):
        return {"users": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"users": []}


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def hash_password(password, salt):
    """scrypt 加盐哈希（标准库实现，不引入第三方依赖）。"""
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=32
    ).hex()


def public_user(u):
    return {"id": u["id"], "username": u["username"], "createdAt": u["createdAt"]}


def stub_items(account_id):
    """主区项目内容占位数据。TODO(团队): 按账号持久化并实现增删改查。"""
    return {
        "_stub": True,
        "note": "框架占位数据：仅用于展示主界面布局，真实的项目增删改查待实现",
        "account": account_id,
        "items": [
            {"id": "demo-1", "name": "示例：现金账户", "type": "cash", "value": 10000, "currency": "CNY", "note": "示例数据"},
            {"id": "demo-2", "name": "示例：股票持仓", "type": "stock", "value": 25000, "currency": "CNY", "note": "示例数据"},
            {"id": "demo-3", "name": "示例：黄金持仓", "type": "metal", "value": 8000, "currency": "CNY", "note": "示例数据"},
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PFAServer/0.2"

    # ---------- 基础工具 ----------

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return None

    def _auth_user(self):
        """校验 Authorization: Bearer <token>，返回 (user, token)，未登录返回 (None, None)。"""
        header = self.headers.get("Authorization") or ""
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        uid = _TOKENS.get(token)
        if not uid:
            return None, None
        for u in load_db()["users"]:
            if u["id"] == uid:
                return u, token
        return None, None

    # ---------- HTTP 方法入口 ----------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self.api(method, path)
        elif method == "GET":
            self.static(path)
        else:
            self._send(404, {"error": "接口不存在"})

    # ---------- API 路由 ----------

    def api(self, method, path):
        if path == "/api/health" and method == "GET":
            return self._send(200, {"ok": True, "name": "pfa-server", "version": "0.2.0"})

        if path == "/api/register" and method == "POST":
            return self.register()
        if path == "/api/login" and method == "POST":
            return self.login()
        if path == "/api/logout" and method == "POST":
            header = self.headers.get("Authorization") or ""
            token = header[7:].strip() if header.startswith("Bearer ") else ""
            _TOKENS.pop(token, None)
            return self._send(200, {"ok": True})

        if path == "/api/panels" and method == "GET":
            try:
                with open(PANELS_FILE, "r", encoding="utf-8") as f:
                    return self._send(200, json.load(f))
            except (OSError, ValueError):
                return self._send(200, {"_stub": True, "note": "占位数据缺失",
                                        "stocks": {"rows": []},
                                        "currencies": {"base": "CNY", "rows": []},
                                        "metals": {"rows": []}})

        # 以下接口需要登录
        user, _token = self._auth_user()
        if user is None:
            return self._send(401, {"error": "未登录或登录已过期"})

        if path == "/api/profile" and method == "GET":
            return self._send(200, {"user": public_user(user)})
        if path == "/api/profile" and method == "PUT":
            # 🚧 框架占位：TODO(团队) 校验并更新 db.json 中对应记录
            return self._send(501, {"error": "功能待实现（框架占位）：修改账号资料"})
        if path == "/api/items" and method == "GET":
            return self._send(200, stub_items(user["id"]))
        if path == "/api/items" and method == "POST":
            return self._send(501, {"error": "功能待实现（框架占位）：新增项目"})
        if path.startswith("/api/items/") and method == "DELETE":
            return self._send(501, {"error": "功能待实现（框架占位）：删除项目"})
        if path == "/api/settings" and method == "GET":
            return self._send(501, {"error": "功能待实现（框架占位）：金融主体设置读取"})
        if path == "/api/settings" and method == "PUT":
            return self._send(501, {"error": "功能待实现（框架占位）：金融主体设置保存"})

        self._send(404, {"error": "接口不存在"})

    def register(self):
        body = self._body()
        if body is None:
            return self._send(400, {"error": "请求体不是合法 JSON"})
        username = str(body.get("username") or "").strip()
        password = body.get("password")
        if not NAME_RE.match(username):
            return self._send(400, {"error": "用户名需为 2-32 位字母、数字、下划线或中文"})
        if not isinstance(password, str) or not 6 <= len(password) <= 64:
            return self._send(400, {"error": "密码长度需为 6-64 位"})
        with _DB_LOCK:
            db = load_db()
            if any(u["username"] == username for u in db["users"]):
                return self._send(409, {"error": "用户名已存在"})
            salt = secrets.token_hex(16)
            user = {
                "id": secrets.token_hex(16),
                "username": username,
                "salt": salt,
                "passHash": hash_password(password, salt),
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            db["users"].append(user)
            save_db(db)
        token = secrets.token_hex(24)
        _TOKENS[token] = user["id"]
        return self._send(201, {"token": token, "user": public_user(user)})

    def login(self):
        body = self._body()
        if body is None:
            return self._send(400, {"error": "请求体不是合法 JSON"})
        username = str(body.get("username") or "").strip()
        password = body.get("password")
        if not isinstance(password, str):
            return self._send(400, {"error": "请提供用户名和密码"})
        user = next((u for u in load_db()["users"] if u["username"] == username), None)
        ok = False
        if user:
            try:
                ok = secrets.compare_digest(hash_password(password, user["salt"]), user["passHash"])
            except ValueError:
                ok = False
        if not ok:
            return self._send(401, {"error": "用户名或密码错误"})
        token = secrets.token_hex(24)
        _TOKENS[token] = user["id"]
        return self._send(200, {"token": token, "user": public_user(user)})

    # ---------- 静态托管（开发便利；客户端与服务器端代码保持分离） ----------

    def static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(CLIENT_DIR, rel))
        if not full.startswith(CLIENT_DIR + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "文件不存在"})
        with open(full, "rb") as f:
            body = f.read()
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[pfa-server] %s" % (fmt % args))


def main():
    print(f"[pfa-server] 已启动: http://127.0.0.1:{PORT}（同时托管客户端页面 {CLIENT_DIR_NAME}/）")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
