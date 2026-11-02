import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import codex_usage_ui as ui
import codex_token_usage as usage
from test_codex_token_usage import event, tokens, write_log


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        write_log(self.home / "sessions" / "a.jsonl", [
            event(tokens(100, 10), tokens(100, 10)),
            event(tokens(300, 30), tokens(200, 20), "2026-11-02T16:00:00Z")])

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_and_analyze_use_the_core_calculation_and_beijing_dates(self):
        exported = ui.local_export(self.home, "测试电脑")
        self.assertEqual(exported["files_scanned"], 1)
        data = ui.analyze([exported])
        self.assertEqual(data["summary"]["total_tokens"], 330)
        self.assertEqual([row["date"] for row in data["daily"]], ["2026-11-02", "2026-11-03"])
        self.assertEqual(ui.analyze([exported], "2026-11-03", "2026-11-03")["summary"]["total_tokens"], 220)

    def test_import_deduplication_and_shared_attribution(self):
        a = ui.local_export(self.home, "办公电脑")
        b = ui.local_export(self.home, "家用电脑")
        result = ui.analyze([a, a, b])
        self.assertEqual(result["summary"]["total_tokens"], 330)
        self.assertEqual(result["summary"]["machines"][0]["machine"], usage.SHARED)

    def test_export_excludes_conversation_and_account_information(self):
        path = self.home / "sessions" / "a.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "response_item", "payload": {"text": "private-content"}}) + "\n")
        exported = ui.local_export(self.home, "测试电脑")
        self.assertNotIn("private-content", exported["text"])
        self.assertNotIn("codex_home", exported["text"])

    def test_invalid_input_and_empty_data(self):
        self.assertEqual(ui.analyze([])["summary"]["total_tokens"], 0)
        for files in [[{"name": "bad", "text": "not-json"}], [{"text": "[]"}], [{}]]:
            with self.assertRaises((ValueError, TypeError)):
                ui.analyze(files)
        with self.assertRaisesRegex(ValueError, "开始日期"):
            ui.analyze([], "2026-11-03", "2026-11-02")

    def test_default_port_conflict_falls_back_to_an_available_port(self):
        fake = SimpleNamespace(server_port=12345)
        with patch.object(ui, "ThreadingHTTPServer", side_effect=[OSError("occupied"), fake]) as factory:
            server = ui.make_server(home=self.home)
        self.assertEqual(factory.call_args_list[0].args[0], ("127.0.0.1", 8765))
        self.assertEqual(factory.call_args_list[1].args[0], ("127.0.0.1", 0))
        self.assertEqual(server.origin, "http://127.0.0.1:12345")

    def test_explicit_port_conflict_is_reported(self):
        with patch.object(ui, "ThreadingHTTPServer", side_effect=OSError("occupied")) as factory:
            with self.assertRaises(OSError):
                ui.make_server(port=9000, home=self.home)
        self.assertEqual(factory.call_count, 1)

    def test_http_routes_and_local_access_controls(self):
        server = ui.make_server(0, self.home)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        def request(method, path, body=None, headers=None):
            values = {"Content-Type": "application/json", "X-Usage-Token": server.request_token}
            values.update(headers or {})
            connection.request(method, path, json.dumps(body).encode() if body is not None else None, values)
            response = connection.getresponse()
            return response.status, response.read(), response.getheaders()

        try:
            status, content, headers = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("用量看板", content.decode())
            self.assertIn("Content-Security-Policy", dict(headers))
            self.assertEqual(request("GET", "/../codex_usage_ui.py")[0], 404)
            self.assertEqual(request("GET", "/", headers={"Host": "evil.test"})[0], 403)
            self.assertEqual(request("POST", "/api/scan", {}, {"X-Usage-Token": "wrong"})[0], 403)
            self.assertEqual(request("POST", "/api/scan", {}, {"Origin": "https://evil.test"})[0], 403)
            status, content, _ = request("POST", "/api/scan", {"machine": "测试电脑"})
            self.assertEqual(status, 200)
            exported = json.loads(content)
            status, content, _ = request("POST", "/api/analyze", {"files": [exported]})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(content)["summary"]["total_tokens"], 330)
            status, content, _ = request("POST", "/api/analyze", {"files": [{"text": "bad"}]})
            self.assertEqual(status, 400)
            self.assertIn("重新导出", json.loads(content)["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
