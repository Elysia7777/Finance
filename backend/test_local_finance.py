import os
import json
import glob
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import bill_parser
from dedup import dedup
import server
import storage
import llm_client


class _MockLLMHandler(BaseHTTPRequestHandler):
    last_body = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        _MockLLMHandler.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
        payload = json.dumps({
            "choices": [{"message": {"role": "assistant",
                                     "content": "根据账单，2026年10月支出 18.00 元。"}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class LocalFinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_path = storage.DB_PATH
        storage.DB_PATH = os.path.join(self.temp_dir.name, "finance.db")
        storage.init_db()
        self.previous_api_key = os.environ.get("LLM_API_KEY")
        os.environ.pop("LLM_API_KEY", None)

    def tearDown(self):
        storage.DB_PATH = self.previous_path
        if self.previous_api_key is not None:
            os.environ["LLM_API_KEY"] = self.previous_api_key
        self.temp_dir.cleanup()

    def test_parse_wechat_csv_and_preserve_income_expense(self):
        bill = (
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,备注\n"
            "2026-10-14 12:30:00,商户消费,面包店,午餐,支出,25.50,零钱,支付成功,A-1,/\n"
            "2026-10-14 13:00:00,退款,商户,订单退款,收入,6.00,零钱,支付成功,A-2,/\n"
        ).encode("utf-8")
        records = bill_parser.parse_bill(bill, "微信账单.csv")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["direction"], "expense")
        self.assertEqual(records[0]["amount"], 25.5)
        self.assertEqual(records[1]["direction"], "income")
        self.assertEqual(records[1]["date"], "2026-10-14")

    def test_import_deduplicates_and_rules_reclassify(self):
        bill = (
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),当前状态\n"
            "2026-10-14 12:30:00,群收款,群收款,/,支出,25,支付成功\n"
        ).encode("utf-8")
        records = bill_parser.parse_bill(bill, "微信账单.csv")
        self.assertEqual(storage.save_transactions(records), (1, 0))
        self.assertEqual(storage.save_transactions(records), (0, 1))
        settings = storage.save_settings({"rules": [{
            "keyword": "群收款", "amount": 25, "category": "水电费", "bucket": "monthly",
        }]})
        self.assertEqual(settings["rules"][0]["category"], "水电费")
        transaction = storage.load_transactions("2026-10")[0]
        self.assertEqual(transaction["category"], "水电费")
        self.assertEqual(transaction["bucket"], "monthly")
        storage.update_transaction(transaction["id"], {"category": "临时调整"})
        storage.save_settings({"theme": "dark"})
        self.assertEqual(storage.load_transactions("2026-10")[0]["category"], "临时调整")

    def test_example_workbook_keeps_distinct_same_amount_rows(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filename = glob.glob(os.path.join(root, "*.xlsx"))[0]
        with open(filename, "rb") as sample:
            records = bill_parser.parse_bill(sample.read(), os.path.basename(filename))
        additions, duplicates = dedup(records)
        self.assertEqual(len(additions), len(records))
        self.assertEqual(duplicates, [])

    def test_profile_theme_and_assets_are_local_data(self):
        profile = storage.save_profile({"displayName": "小明", "avatar": "🐼"})
        self.assertEqual(profile["displayName"], "小明")
        settings = storage.save_settings({"theme": "mint", "largeThreshold": 800})
        self.assertEqual(settings["theme"], "mint")
        self.assertEqual(settings["largeThreshold"], 800)
        item = storage.save_item({
            "name": "指数基金", "type": "fund", "quantity": 2,
            "costPrice": 10, "currentPrice": 12,
        })
        self.assertEqual(item["value"], 24)
        self.assertEqual(storage.load_items()[0]["id"], item["id"])

    def test_qa_uses_llm_when_configured(self):
        llm_httpd = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLMHandler)
        thread = threading.Thread(target=llm_httpd.serve_forever, daemon=True)
        thread.start()
        previous = {key: os.environ.get(key)
                    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:%s/v1" % llm_httpd.server_address[1]
        os.environ["LLM_MODEL"] = "mock-model"
        try:
            result = llm_client.ask("2026年10月支出多少？", [{
                "id": "1", "date": "2026-10-14", "amount": 18,
                "direction": "expense", "category": "餐饮", "merchant": "小店", "note": "",
            }], history=[
                {"role": "user", "content": "我之前问过支出"},
                {"role": "assistant", "content": "上一条回答"},
            ])
            self.assertEqual(result["source"], "llm")
            self.assertEqual(result["model"], "mock-model")
            self.assertIn("18.00", result["answer"])
            self.assertEqual(result["fallbackAnswer"], "2026年10月共支出 ¥18.00，共 1 笔")
            messages = _MockLLMHandler.last_body["messages"]
            self.assertEqual(messages[0]["role"], "system")
            self.assertEqual(messages[1], {"role": "user", "content": "我之前问过支出"})
            self.assertEqual(messages[2], {"role": "assistant", "content": "上一条回答"})
            self.assertEqual(messages[-1]["role"], "user")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            llm_httpd.shutdown()
            llm_httpd.server_close()
            thread.join()

    def test_local_http_api_uploads_without_authentication(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            root = "http://127.0.0.1:%s" % httpd.server_address[1]
            with urlopen(root + "/api/profile") as response:
                self.assertEqual(response.status, 200)
            headers = (
                b"--finance-boundary\r\n"
                b'Content-Disposition: form-data; name="file"; filename="bill.csv"\r\n'
                b"Content-Type: text/csv\r\n\r\n"
            )
            content = (
                "交易时间,交易类型,交易对方,商品,收/支,金额(元),当前状态\n"
                "2026-10-14,商户消费,小店,茶饮,支出,18,支付成功\n"
            ).encode("utf-8")
            body = headers + content + b"\r\n--finance-boundary--\r\n"
            request = Request(
                root + "/api/bills/wechat", data=body, method="POST",
                headers={"Content-Type": "multipart/form-data; boundary=finance-boundary"},
            )
            with urlopen(request) as response:
                result = response.read().decode("utf-8")
                self.assertEqual(response.status, 201)
                self.assertIn('"imported": 1', result)
            with urlopen(root + "/api/transactions?month=2026-10") as response:
                self.assertIn("小店", response.read().decode("utf-8"))
            with urlopen(root + "/api/qa/status") as response:
                status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(status.get("configured"), False)
            with urlopen(root + "/api/llm/config") as response:
                llm_config = json.loads(response.read().decode("utf-8"))
                self.assertEqual(llm_config.get("enabled"), False)
                self.assertEqual(llm_config.get("hasApiKey"), False)
            qa_body = json.dumps({"question": "2026年10月支出多少？"}).encode("utf-8")
            qa_request = Request(
                root + "/api/qa", data=qa_body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(qa_request) as response:
                qa_result = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(qa_result.get("source"), "local")
                self.assertIn("18", qa_result["answer"])
            conversation_request = Request(
                root + "/api/conversations", data=b"", method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(conversation_request) as response:
                conversation = json.loads(response.read().decode("utf-8"))["conversation"]
                self.assertIn("id", conversation)
            conversation_id = conversation["id"]
            message_request = Request(
                root + "/api/conversations/%s/messages" % conversation_id,
                data=json.dumps({"question": "这个月花了多少？"}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(message_request) as response:
                message = json.loads(response.read().decode("utf-8"))["message"]
                self.assertEqual(message["role"], "assistant")
                self.assertIn("18", message["content"])
            with urlopen(root + "/api/conversations") as response:
                conversations = json.loads(response.read().decode("utf-8"))["conversations"]
                self.assertEqual(conversations[0]["title"], "这个月花了多少？")
                self.assertEqual(conversations[0]["messageCount"], 2)
            with urlopen(root + "/api/conversations/" + conversation_id) as response:
                detail = json.loads(response.read().decode("utf-8"))
                self.assertEqual(len(detail["messages"]), 2)
            delete_request = Request(
                root + "/api/conversations/" + conversation_id, method="DELETE",
            )
            with urlopen(delete_request) as response:
                self.assertEqual(response.status, 200)
            llm_save = Request(
                root + "/api/llm/config",
                data=json.dumps({
                    "enabled": True,
                    "baseUrl": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "timeout": 30,
                    "apiKey": "sk-test",
                }).encode("utf-8"),
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(llm_save) as response:
                llm_saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(llm_saved["enabled"], True)
                self.assertEqual(llm_saved["model"], "deepseek-chat")
                self.assertEqual(llm_saved["hasApiKey"], True)
                self.assertNotIn("apiKey", llm_saved)
            with self.assertRaises(HTTPError) as error:
                urlopen(root + "/api/login")
            self.assertEqual(error.exception.code, 404)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(root + "/api/bills/alipay", data=b"", method="POST"))
            self.assertEqual(error.exception.code, 503)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
