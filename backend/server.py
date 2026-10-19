#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-person local finance server; no accounts or remote data services."""

import json
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import bill_parser
import qa
import storage

HOST = os.environ.get("PFA_HOST", "127.0.0.1")
PORT = int(os.environ.get("PFA_PORT", "8000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "frontend"))
AI_DOCS_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "ai_docs"))
if AI_DOCS_DIR not in sys.path:
    sys.path.insert(0, AI_DOCS_DIR)
import llm_client  # noqa: E402
import market  # noqa: E402
MAX_BODY = 20 * 1024 * 1024
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "FinanceLocal/1.0"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ValueError("请求长度无效")
        if length < 0:
            raise ValueError("请求长度无效")
        if length > MAX_BODY:
            self.rfile.read(length)
            return None
        return self.rfile.read(length)

    def _json_body(self):
        raw = self._read_body()
        if raw is None:
            raise ValueError("请求不得超过 20 MB")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("请求体不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self.api(method, path, parse_qs(parsed.query))
            elif method == "GET":
                self.static(path)
            else:
                self._send(404, {"error": "接口不存在"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})

    def _multipart_file(self):
        content_type = self.headers.get("Content-Type", "")
        raw = self._read_body()
        if raw is None:
            raise ValueError("上传文件不得超过 20 MB")
        if not content_type.lower().startswith("multipart/form-data;"):
            raise ValueError("账单请以 multipart/form-data 格式上传")
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + raw
        )
        for part in message.iter_parts():
            if part.get_content_disposition() == "form-data" and part.get_filename():
                filename = os.path.basename(part.get_filename().replace("\\", "/"))
                content = part.get_payload(decode=True)
                if filename and content:
                    return filename, content
        raise ValueError("没有收到账单文件")

    def api(self, method, path, query):
        if path == "/api/health" and method == "GET":
            return self._send(200, {"ok": True, "name": "finance-local", "version": "1.0.0"})
        if path == "/api/panels" and method == "GET":
            return self._send(200, market.get_panels())
        if path == "/api/profile":
            if method == "GET":
                return self._send(200, {"profile": storage.get_profile()})
            if method == "PUT":
                return self._send(200, {"profile": storage.save_profile(self._json_body())})
        if path == "/api/settings":
            if method == "GET":
                return self._send(200, storage.get_settings())
            if method == "PUT":
                return self._send(200, storage.save_settings(self._json_body()))
        if path == "/api/transactions" and method == "GET":
            month = (query.get("month") or [None])[0]
            if month and not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError("月份格式应为 YYYY-MM")
            keyword = (query.get("q") or [""])[0].strip()
            if len(keyword) > 100:
                raise ValueError("搜索关键词不能超过 100 个字符")
            return self._send(200, {
                "transactions": storage.load_transactions(month, keyword or None)
            })
        if path == "/api/llm/config":
            if method == "GET":
                return self._send(200, storage.get_llm_public_config())
            if method == "PUT":
                storage.save_llm_config(self._json_body())
                return self._send(200, storage.get_llm_public_config())
        if path == "/api/qa/status" and method == "GET":
            return self._send(200, llm_client.status(storage.get_llm_config()))
        if path == "/api/qa" and method == "POST":
            body = self._json_body()
            question = str(body.get("question") or "").strip()
            if not question or len(question) > 200:
                raise ValueError("问题需为 1-200 个字符")
            result = llm_client.ask(
                question, storage.load_transactions(), storage.get_settings(),
                storage.get_llm_config(),
            )
            return self._send(200, result)
        if path == "/api/conversations":
            if method == "GET":
                return self._send(200, {"conversations": storage.list_conversations()})
            if method == "POST":
                return self._send(201, {"conversation": storage.create_conversation()})
        message_route = re.fullmatch(
            r"/api/conversations/([a-fA-F0-9]+)/messages", path
        )
        if message_route and method == "POST":
            conversation_id = message_route.group(1)
            body = self._json_body()
            question = str(body.get("question") or "").strip()
            if not question or len(question) > 400:
                raise ValueError("问题需为 1-400 个字符")
            conversation = storage.get_conversation_messages(conversation_id)
            if conversation is None:
                return self._send(404, {"error": "找不到这个对话"})
            history = [
                {"role": item["role"], "content": item["content"]}
                for item in conversation["messages"]
            ]
            result = llm_client.ask(
                question, storage.load_transactions(), storage.get_settings(),
                storage.get_llm_config(), history,
            )
            storage.add_message(conversation_id, "user", question)
            message = storage.add_message(
                conversation_id, "assistant", result["answer"],
                source=result.get("source", "local"),
                model=result.get("model", ""),
                evidence=result.get("evidence", []),
            )
            return self._send(200, {"message": message})
        conversation_route = re.fullmatch(
            r"/api/conversations/([a-fA-F0-9]+)", path
        )
        if conversation_route:
            conversation_id = conversation_route.group(1)
            if method == "GET":
                conversation = storage.get_conversation_messages(conversation_id)
                if conversation is None:
                    return self._send(404, {"error": "找不到这个对话"})
                return self._send(200, conversation)
            if method == "DELETE":
                if not storage.delete_conversation(conversation_id):
                    return self._send(404, {"error": "找不到这个对话"})
                return self._send(200, {"ok": True})
        if path == "/api/reminders" and method == "GET":
            reminders = qa.build_reminders(storage.load_transactions(), storage.get_settings())
            return self._send(200, {"reminders": reminders})
        match = re.fullmatch(r"/api/transactions/([a-fA-F0-9]+)", path)
        if match and method == "PUT":
            transaction = storage.update_transaction(match.group(1), self._json_body())
            if transaction is None:
                return self._send(404, {"error": "找不到这笔流水"})
            return self._send(200, {"transaction": transaction})
        if match and method == "DELETE":
            if not storage.delete_transaction(match.group(1)):
                return self._send(404, {"error": "找不到这笔流水"})
            return self._send(200, {"ok": True})
        bill_route = re.fullmatch(r"/api/bills/(wechat|alipay|unionpay)", path)
        if (path == "/api/bills" or bill_route) and method == "POST":
            platform = bill_route.group(1) if bill_route else "wechat"
            if platform == "unionpay":
                return self._send(503, {"error": "银联/云闪付导入暂未开放，需按样本适配表头"})
            source = "支付宝" if platform == "alipay" else "微信"
            filename, content = self._multipart_file()
            parsed = bill_parser.parse_bill(content, filename, source=source)
            imported, duplicates = storage.save_transactions(parsed)
            return self._send(201, {
                "imported": imported,
                "duplicates": duplicates,
                "parsed": len(parsed),
            })
        if path == "/api/items":
            if method == "GET":
                return self._send(200, {"items": storage.load_items()})
            if method == "POST":
                return self._send(201, {"item": storage.save_item(self._json_body())})
        if path == "/api/quote" and method == "GET":
            kind = (query.get("type") or [""])[0].strip().lower()
            code = (query.get("code") or [""])[0].strip()
            if not code or len(code) > 20:
                raise ValueError("请提供有效的代码")
            if kind == "stock":
                return self._send(200, market.lookup_stock(code))
            if kind == "fund":
                return self._send(200, market.lookup_fund(code))
            if kind == "cash":
                return self._send(200, {
                    "code": code, "price": 1, "source": "现金", "time": "",
                })
            raise ValueError("仅支持股票 / 基金 / 现金类型自动获取价格")
        match = re.fullmatch(r"/api/items/([a-fA-F0-9]+)", path)
        if match and method == "PUT":
            item = storage.update_item(match.group(1), self._json_body())
            if item is None:
                return self._send(404, {"error": "找不到这项资产"})
            return self._send(200, {"item": item})
        if match and method == "DELETE":
            if not storage.delete_item(match.group(1)):
                return self._send(404, {"error": "找不到这项资产"})
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "接口不存在"})

    def static(self, path):
        relative = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(CLIENT_DIR, relative))
        if not full.startswith(CLIENT_DIR + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "文件不存在"})
        with open(full, "rb") as file:
            body = file.read()
        extension = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(extension, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[finance-local] %s" % (fmt % args))


def main():
    storage.init_db()
    print("[finance-local] 本机数据服务已启动：http://%s:%s" % (HOST, PORT))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
