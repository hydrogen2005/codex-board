#!/usr/bin/env python3
"""Codex token 中文本地界面。只监听本机，不上传数据。"""

import argparse
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from types import SimpleNamespace
import webbrowser

import codex_token_usage as usage

STATIC = Path(__file__).resolve().parent / "ui"
LIMIT = 64 * 1024 * 1024


def local_export(home, machine):
    machine = machine.strip()
    records, diagnostics, file_count = usage.collect_local(home, machine)
    header = {"type": "header", "schema": usage.SCHEMA, "machine": machine,
              "exported_at_utc": datetime.now(timezone.utc).isoformat(),
              "files_scanned": file_count, "diagnostics": diagnostics.as_dict()}
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in
                     [header, *({"type": "usage", **row} for row in records)]) + "\n"
    return {"name": "本机.usage.jsonl", "text": text, "machine": machine,
            "files_scanned": file_count, "event_count": len(records)}


def analyze(files, start=None, end=None):
    if not isinstance(files, list) or len(files) > 100:
        raise ValueError("每次最多导入 100 份统计文件。")
    streams, sources = [], []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("统计文件内容无效。")
        name = item.get("name", "统计文件")
        if not isinstance(name, str):
            raise ValueError("文件名称无效。")
        text = item["text"].lstrip("\ufeff")
        header = json.loads(text.split("\n", 1)[0])
        if not isinstance(header, dict):
            raise ValueError("统计文件头格式无效。")
        sources.append({"name": name, "machine": header.get("machine", "未知设备"),
                        "from": header.get("from"), "to": header.get("to"),
                        "exported_at_utc": header.get("exported_at_utc")})
        streams.append((name, io.StringIO(text)))
    start = date.fromisoformat(start) if start else None
    end = date.fromisoformat(end) if end else None
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期。")
    diagnostics = usage.Diagnostics()
    records, machines = usage.load_export_streams(streams, diagnostics)
    summary, daily = usage.summarize(records, SimpleNamespace(
        start=start, end=end, utc_offset=usage.utc_offset("+08:00")))
    all_days = sorted({datetime.fromisoformat(row["timestamp_utc"]).astimezone(
        usage.utc_offset("+08:00")).date().isoformat() for row in records})
    return {"summary": summary, "daily": daily, "sources": sources,
            "exported_machines": machines, "diagnostics": diagnostics.as_dict(),
            "available_from": all_days[0] if all_days else None,
            "available_to": all_days[-1] if all_days else None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Never log payloads or export contents.

    def send_content(self, status, content, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def json(self, status, data):
        self.send_content(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")

    def valid_host(self):
        return self.headers.get("Host") == self.server.address_label

    def do_GET(self):
        if not self.valid_host():
            return self.json(403, {"error": "仅允许从本机访问。"})
        if self.path == "/api/config":
            return self.json(200, {"machine": socket.gethostname(),
                "codex_home": str(self.server.codex_home), "token": self.server.request_token,
                "today": datetime.now(usage.utc_offset("+08:00")).date().isoformat()})
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/ui.js": ("ui.js", "text/javascript; charset=utf-8"),
                 "/ui.css": ("ui.css", "text/css; charset=utf-8")}
        if self.path not in files:
            return self.json(404, {"error": "页面不存在。"})
        filename, mime = files[self.path]
        self.send_content(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if (not self.valid_host() or self.headers.get("X-Usage-Token") != self.server.request_token
                or self.headers.get("Origin", self.server.origin) != self.server.origin):
            return self.json(403, {"error": "请求来源无效，请刷新本机页面。"})
        if self.path not in ("/api/scan", "/api/analyze"):
            return self.json(404, {"error": "接口不存在。"})
        try:
            if self.headers.get_content_type() != "application/json":
                return self.json(415, {"error": "请使用 JSON 请求。"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= LIMIT:
                return self.json(413, {"error": "请求为空或超过 64 MB，请减少导入文件数量。"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求内容无效。")
            if self.path == "/api/scan":
                machine = data.get("machine") or socket.gethostname()
                home = data.get("codex_home") or str(self.server.codex_home)
                if not isinstance(machine, str) or not isinstance(home, str):
                    raise ValueError("电脑名称或日志目录无效。")
                return self.json(200, local_export(Path(home).expanduser(), machine.strip()))
            return self.json(200, analyze(data.get("files", []), data.get("from"), data.get("to")))
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            return self.json(400, {"error": friendly_error(exc)})


def friendly_error(exc):
    message = str(exc)
    if "no session JSONL files" in message:
        return "未找到 Codex 会话日志。请在「本机设置」中检查日志目录。"
    if "conflicting exports" in message:
        return "同一事件的统计数值发生冲突，请重新导出对应电脑的数据。"
    if "not a supported export" in message:
        return "文件格式不支持，请导入本工具生成的 .usage.jsonl 文件。"
    if isinstance(exc, json.JSONDecodeError):
        return "文件内容不是完整的 JSON 统计数据，请重新导出后再导入。"
    if isinstance(exc, PermissionError):
        return "无法读取日志目录，请检查本机文件权限。"
    if "choose a nonempty" in message:
        return "请输入有效且唯一的电脑名称。"
    if "invalid" in message or "missing" in message or "counter" in message:
        return "统计文件字段无效，请使用原始导出文件重试。"
    return "操作失败：" + message


def make_server(port=None, home=None):
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8765 if port is None else port), Handler)
    except OSError:
        if port is not None:
            raise
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.address_label = "127.0.0.1:" + str(server.server_port)
    server.origin = "http://" + server.address_label
    server.request_token = secrets.token_urlsafe(32)
    server.codex_home = home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    return server


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="本机端口，默认 8765；占用时自动选择空闲端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--codex-home", type=Path, help="指定 Codex 数据目录")
    args = parser.parse_args()
    try:
        server = make_server(args.port, args.codex_home)
    except OSError as exc:
        print("启动失败：", exc, "。请尝试 --port 8766。", file=sys.stderr)
        return 1
    print("Codex token 统计界面：" + server.origin, flush=True)
    print("仅本机访问。保持此窗口打开；关闭此窗口或按 Ctrl+C 退出。", flush=True)
    if not args.no_browser:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
