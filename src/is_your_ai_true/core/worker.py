"""后台请求线程。

在独立 QThread 中执行端点请求，通过 Qt 信号把增量正文、思维链、
用量、原始事件、统计结果与错误回报给 UI 线程；同时为每次请求
构建 ResponseRecord（模型 ID 核验 / 指纹 / 完整结构）并写入归档。
"""

import json
import threading
from typing import Any

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from .archive import Archive
from .events import (
    DeltaEvent,
    ErrorEvent,
    MetadataEvent,
    ReasoningEvent,
    UsageEvent,
)
from .models import ChatMessage, EndpointConfig, RequestParams
from .providers import get_provider
from .record import ResponseRecord
from .sse import is_done_marker, parse_sse_stream
from .stats import RequestStats, Usage


class _ProtocolError(Exception):
    """流中的 ErrorEvent 转换而来的异常。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class _HttpCallError(Exception):
    """HTTP 层错误。detail 在响应被关闭前已抓取，可直接展示。"""

    def __init__(self, message: str, detail: str = "", status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code


def ensure_ok(response: requests.Response) -> None:
    """状态码 >= 400 时抛出 _HttpCallError。

    必须在 response 关闭前调用：错误体会在此被读出，避免流式
    响应 close 后无法再读取的问题。
    """
    if response.status_code < 400:
        return
    message = f"HTTP {response.status_code} {response.reason}".strip()
    detail = ""
    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or message)
        elif isinstance(error, str):
            message = error
        detail = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        detail = (response.text or "")[:2000]
    raise _HttpCallError(message, detail, response.status_code)


class RequestWorker(QThread):
    delta = pyqtSignal(str)  # 增量正文（非流式时为完整文本）
    reasoning = pyqtSignal(str)  # 增量思维链
    usage = pyqtSignal(int, int)  # 输入 / 输出 tokens
    debug = pyqtSignal(str, dict)  # 原始事件（名称 + 已解析 data）
    failed = pyqtSignal(str, str)  # 错误消息 + 详情
    cancelled = pyqtSignal()  # 用户主动停止
    succeeded = pyqtSignal(dict, dict)  # 统计 + 记录摘要（核验信息）
    finished = pyqtSignal()  # 无论成败都发出，UI 恢复状态用

    def __init__(
        self,
        config: EndpointConfig,
        messages: list[ChatMessage],
        params: RequestParams,
        archive: Archive | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._messages = list(messages)
        self._params = params
        self._provider = get_provider(config.protocol)
        self._archive = archive
        self._cancel = threading.Event()
        self._output_text = ""
        self._reasoning_text = ""

    def cancel(self) -> None:
        """从 UI 线程请求停止（线程安全）。"""
        self._cancel.set()

    def run(self) -> None:  # noqa: PLR0912 - 分支多为异常归类
        stats = RequestStats()
        stats.mark_started()
        record = ResponseRecord.from_request(
            self._config, self._messages, self._params
        )
        try:
            url, headers, body = self._provider.build_request(
                self._config, self._messages, self._params
            )
            record.request_body = body
        except ValueError as exc:
            message = f"配置错误：{exc}"
            record.mark_failed(message)
            self._archive_append(record)
            self.failed.emit(message, "")
            self.finished.emit()
            return

        try:
            if self._params.stream:
                self._run_stream(url, headers, body, stats, record)
            else:
                self._run_blocking(url, headers, body, stats, record)
            record.output_text = self._output_text
            record.reasoning_text = self._reasoning_text
            record.usage = (
                {
                    "input_tokens": stats.usage.input_tokens,
                    "output_tokens": stats.usage.output_tokens,
                }
                if stats.usage is not None
                else None
            )
            if self._cancel.is_set():
                record.mark_cancelled()
                path = self._archive_append(record)
                _ = path
                self.cancelled.emit()
            else:
                stats.mark_finished()
                path = self._archive_append(record)
                self.succeeded.emit(
                    stats.to_dict(),
                    record.summary(str(path) if path else None),
                )
        except _HttpCallError as exc:
            record.output_text = self._output_text
            record.reasoning_text = self._reasoning_text
            record.mark_failed(exc.message)
            self._archive_append(record)
            self.failed.emit(exc.message, exc.detail)
        except requests.RequestException as exc:
            message = f"请求失败：{exc.__class__.__name__}"
            record.mark_failed(message)
            self._archive_append(record)
            self.failed.emit(message, str(exc))
        except _ProtocolError as exc:
            record.output_text = self._output_text
            record.reasoning_text = self._reasoning_text
            record.mark_failed(exc.message)
            self._archive_append(record)
            self.failed.emit(exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001 - 兜底，确保 UI 不会卡在等待态
            message = f"内部错误：{exc.__class__.__name__}"
            record.mark_failed(message)
            self._archive_append(record)
            self.failed.emit(message, str(exc))
        finally:
            self.finished.emit()

    # ---- 请求执行 -----------------------------------------------------------

    def _run_stream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        stats: RequestStats,
        record: ResponseRecord,
    ) -> None:
        # 不使用 with：抛错时 with 会先关闭响应，导致上层无法读取
        # 错误体。改由 ensure_ok 在关闭前抓取（见 _run_blocking 也是同样思路）。
        response = requests.post(
            url, headers=headers, json=body, stream=True, timeout=self._config.timeout
        )
        try:
            ensure_ok(response)
            for event in parse_sse_stream(response.iter_lines(decode_unicode=True)):
                if self._cancel.is_set():
                    return
                if is_done_marker(event.data):
                    return
                data = load_json_object(event.data)
                if data is None:
                    continue
                self.debug.emit(event.name, data)
                record.append_event(event.name, data)
                self._dispatch_event(event.name, data, stats, record)
        finally:
            response.close()

    def _run_blocking(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        stats: RequestStats,
        record: ResponseRecord,
    ) -> None:
        response = requests.post(
            url, headers=headers, json=body, timeout=self._config.timeout
        )
        ensure_ok(response)
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {"__value__": payload}
        self.debug.emit("response", payload)
        record.set_payload(payload)
        result = self._provider.parse_non_stream(payload)
        record.apply_non_stream(result)
        if result.usage is not None:
            stats.usage = result.usage
            self.usage.emit(result.usage.input_tokens, result.usage.output_tokens)
        if result.text:
            stats.record_delta(result.text)
            self.delta.emit(result.text)
        if result.reasoning:
            self.reasoning.emit(result.reasoning)

    def _dispatch_event(
        self, name: str, data: dict[str, Any], stats: RequestStats, record: ResponseRecord
    ) -> None:
        for stream_event in self._provider.iter_stream_events(name, data):
            if isinstance(stream_event, DeltaEvent):
                if stream_event.text:
                    stats.mark_first_token()
                    stats.record_delta(stream_event.text)
                    self._output_text += stream_event.text
                    self.delta.emit(stream_event.text)
            elif isinstance(stream_event, ReasoningEvent):
                if stream_event.text:
                    self._reasoning_text += stream_event.text
                    self.reasoning.emit(stream_event.text)
            elif isinstance(stream_event, UsageEvent):
                prev_input = stats.usage.input_tokens if stats.usage else 0
                prev_output = stats.usage.output_tokens if stats.usage else 0
                stats.usage = Usage(
                    input_tokens=stream_event.input_tokens or prev_input,
                    output_tokens=stream_event.output_tokens or prev_output,
                )
                self.usage.emit(stats.usage.input_tokens, stats.usage.output_tokens)
            elif isinstance(stream_event, MetadataEvent):
                record.apply_metadata(stream_event)
            elif isinstance(stream_event, ErrorEvent):
                raise _ProtocolError(
                    stream_event.message,
                    json.dumps(data, ensure_ascii=False),
                )

    def _archive_append(self, record: ResponseRecord) -> Any:
        """best-effort 归档：失败只记录到调试流，不阻断主流程。"""
        if self._archive is None:
            return None
        try:
            return self._archive.append(record)
        except OSError as exc:
            self.debug.emit("archive_error", {"error": str(exc)})
            return None


def load_json_object(raw: str) -> dict[str, Any] | None:
    """解析 SSE data 文本；非 JSON 或非对象返回 None。"""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else {"__value__": value}
