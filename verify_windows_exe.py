"""Verify a bundled EXE from an unrelated directory with Python removed from PATH."""

import argparse
import http.client
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading


def verify(executable):
    executable = executable.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="codex-exe-check-") as temporary:
        root = Path(temporary)
        home = root / "fixture-home"
        sessions = home / "sessions"
        sessions.mkdir(parents=True)
        counters = {"input_tokens": 1000, "output_tokens": 100, "cached_input_tokens": 500,
                    "reasoning_output_tokens": 30, "total_tokens": 1100}
        events = [{"type": "session_meta", "payload": {"id": "packaged-exe-verification"}},
                  {"type": "event_msg", "timestamp": "2026-11-02T00:00:00Z", "payload": {
                      "type": "token_count", "info": {
                          "total_token_usage": counters, "last_token_usage": counters}}}]
        (sessions / "fixture.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in ("PYTHONHOME", "PYTHONPATH", "PATH", "CODEX_HOME")}
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        output = queue.Queue()
        process = subprocess.Popen(
            [str(executable), "--no-browser", "--port", "0", "--codex-home", str(home)],
            cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)

        def read_output():
            for line in process.stdout:
                output.put(line.rstrip())
            output.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        connection = None
        try:
            line = output.get(timeout=60)
            match = re.search(r"http://127\.0\.0\.1:(\d+)", line or "")
            if not match:
                raise RuntimeError("EXE did not publish a local address: " + str(line))
            connection = http.client.HTTPConnection("127.0.0.1", int(match[1]), timeout=30)

            def request(method, path, body=None, token=None):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["X-Usage-Token"] = token
                connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
                response = connection.getresponse()
                data = response.read()
                if response.status != 200:
                    raise RuntimeError(path + " returned " + str(response.status) + ": " + data.decode())
                return data

            for path, expected in [("/", "Codex 用量概览"), ("/ui.js", "analyzeSources"), ("/ui.css", ".sidebar")]:
                if expected not in request("GET", path).decode("utf-8"):
                    raise RuntimeError("Bundled resource missing: " + path)
            config = json.loads(request("GET", "/api/config"))
            exported = json.loads(request("POST", "/api/scan", {"machine": "EXE验证电脑"}, config["token"]))
            result = json.loads(request("POST", "/api/analyze", {"files": [exported, exported]}, config["token"]))
            if result["summary"]["total_tokens"] != 1100:
                raise RuntimeError("Unexpected token total in bundled EXE")
            if result["summary"]["machines"][0]["share_percent"] != "100.000000":
                raise RuntimeError("Unexpected machine share in bundled EXE")
            print("PASS: standalone EXE; Python absent from PATH; unrelated working directory")
            print("PASS: bundled HTML/CSS/JS, scan/export/import, duplicate removal, totals and shares")
        finally:
            if connection:
                connection.close()
            # A one-file EXE owns a bootloader + child process; terminate this exact tree.
            if process.poll() is None:
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                                "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            process.wait(timeout=15)
            reader.join(timeout=5)
            process.stdout.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    verify(parser.parse_args().executable)
