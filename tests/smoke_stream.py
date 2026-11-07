"""端到端冒烟：本地 SSE 模拟服务器 + RequestWorker + Qt 事件循环。

验证完整链路：worker 线程发请求 -> SSE 解析 -> 思维链/正文信号 ->
模型 ID 核验与指纹提取 -> 归档落盘；并验证 HTTP 错误（401）路径。
运行：uv run python tests/smoke_stream.py
"""

import http.server
import json
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from is_your_ai_true.core.archive import Archive
from is_your_ai_true.core.models import (
    PROTOCOL_OPENAI_CHAT,
    ChatMessage,
    EndpointConfig,
    RequestParams,
)
from is_your_ai_true.core.worker import RequestWorker

CHUNKS = [
    {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "choices": [
            {"index": 0, "delta": {"reasoning_content": "先想一下"}, "finish_reason": None}
        ],
    },
    {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "choices": [{"index": 0, "delta": {"content": "你"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
    {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "system_fingerprint": "fp_smoke",
        "service_tier": "default",
        "choices": [],
        "usage": {"prompt_tokens": 9, "completion_tokens": 2},
    },
]


class MockHandler(http.server.BaseHTTPRequestHandler):
    # 用默认 HTTP/1.0：每次请求后关闭连接，避免 keep-alive 残留字节
    # 触发 BaseHTTPRequestHandler 的 parse_request 异常。

    def do_POST(self) -> None:  # noqa: N802 - http.server 命名
        if "/fail" in self.path:
            payload = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in CHUNKS)
        body += "data: [DONE]\n\n"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


class Results:
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.reasoning: list[str] = []
        self.usage: tuple[int, int] | None = None
        self.stats: dict[str, str] | None = None
        self.record: dict | None = None
        self.error: tuple[str, str] | None = None
        self.cancelled = False

    def handle_delta(self, text: str) -> None:
        self.deltas.append(text)

    def handle_reasoning(self, text: str) -> None:
        self.reasoning.append(text)

    def handle_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.usage = (input_tokens, output_tokens)

    def handle_succeeded(self, stats: dict, record: dict) -> None:
        self.stats = stats
        self.record = record

    def handle_failed(self, message: str, detail: str) -> None:
        self.error = (message, detail)

    def handle_cancelled(self) -> None:
        self.cancelled = True


def run_worker(
    app: QApplication, port: int, archive: Archive, base_suffix: str = ""
) -> Results:
    results = Results()
    worker = RequestWorker(
        EndpointConfig(
            protocol=PROTOCOL_OPENAI_CHAT,
            base_url=f"http://127.0.0.1:{port}{base_suffix}",
            api_key="sk-test",
            model="test-model",
        ),
        [ChatMessage(role="user", content="你好")],
        RequestParams(),
        archive=archive,
    )
    worker.delta.connect(results.handle_delta)
    worker.reasoning.connect(results.handle_reasoning)
    worker.usage.connect(results.handle_usage)
    worker.succeeded.connect(results.handle_succeeded)
    worker.failed.connect(results.handle_failed)
    worker.cancelled.connect(results.handle_cancelled)
    worker.finished.connect(app.quit)

    guard = QTimer()
    guard.setSingleShot(True)
    guard.setInterval(15000)
    guard.timeout.connect(app.quit)
    guard.start()
    worker.start()
    app.exec()
    worker.wait(3000)
    worker.deleteLater()
    return results


def main() -> int:
    failures: list[str] = []
    app = QApplication(sys.argv)
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), MockHandler)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    tmp = tempfile.mkdtemp(prefix="iyat-archive-")
    archive = Archive(Path(tmp) / "records")

    try:
        # 1) 正常流式路径（含思维链与指纹）
        ok = run_worker(app, port, archive)
        if ok.deltas != ["你", "好"]:
            failures.append(f"增量文本不匹配: {ok.deltas}")
        if ok.reasoning != ["先想一下"]:
            failures.append(f"思维链不匹配: {ok.reasoning}")
        if ok.usage != (9, 2):
            failures.append(f"用量不匹配: {ok.usage}")
        if ok.error is not None:
            failures.append(f"不应报错: {ok.error}")
        if ok.stats is None:
            failures.append("缺少统计结果")
        elif ok.stats.get("首 token (TTFT)") == "--":
            failures.append("TTFT 未统计")
        if ok.record is None:
            failures.append("缺少核验记录")
        else:
            record = ok.record
            if record.get("model_match") is not True:
                failures.append(f"模型核验不匹配: {record.get('model_match')}")
            if record.get("reported_model") != "test-model":
                failures.append(f"报告模型不匹配: {record.get('reported_model')}")
            if record.get("system_fingerprint") != "fp_smoke":
                failures.append(f"指纹未提取: {record.get('system_fingerprint')}")
            if record.get("service_tier") != "default":
                failures.append(f"服务层级未提取: {record.get('service_tier')}")
            if not record.get("archive_path"):
                failures.append("未归档")

        # 2) HTTP 错误路径（同样落盘）
        err = run_worker(app, port, archive, base_suffix="/fail")
        if err.error is None:
            failures.append("401 未产生 failed 信号")
        elif "invalid api key" not in err.error[0]:
            failures.append(
                f"错误消息未提取: {err.error[0]} | detail={err.error[1][:300]}"
            )
        if err.stats is not None:
            failures.append("失败路径不应产生统计")
    finally:
        server.shutdown()
        thread.join(timeout=2)

    # 3) 归档读回：两条记录（成功 + 失败），结构完整
    try:
        records = list(archive.iter_records())
    except OSError as exc:
        failures.append(f"归档读回失败: {exc}")
        records = []
    if len(records) != 2:
        failures.append(f"归档条数应为 2，实际 {len(records)}")
    else:
        first = records[0]
        if first["status"] != "success":
            failures.append(f"首条状态应为 success: {first['status']}")
        if first["response"]["system_fingerprint"] != "fp_smoke":
            failures.append("归档中指纹缺失")
        if first["request"]["api_key_hint"] == "sk-test":
            failures.append("归档中 API Key 未脱敏")
        # 流式请求的完整结构应包含全部 chunk 事件（[DONE] 非 JSON，不计数）
        events = first["raw"].get("events") or []
        if len(events) != len(CHUNKS):
            failures.append(f"归档事件数异常: {len(events)}")
        if records[1]["status"] != "failed":
            failures.append(f"次条状态应为 failed: {records[1]['status']}")

    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("PASS: 端到端冒烟（流式/思维链/指纹/核验/归档/错误路径）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
