"""Server-Sent Events (SSE) 流解析。

按 RFC 剖析 `event:` / `data:` 行，以空行作为事件分隔，
兼容多行 data 与注释行。输出 (事件名, 原始 data 文本)，
JSON 反序列化交给调用方，以便识别 `[DONE]` 等特殊值。
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass


@dataclass
class SseEvent:
    name: str
    data: str


def parse_sse_stream(lines: Iterable[str]) -> Iterator[SseEvent]:
    """从逐行文本（如 requests 的 iter_lines 输出）解析 SSE 事件。"""
    event_name = ""
    data_lines: list[str] = []

    def flush() -> Iterator[SseEvent]:
        if data_lines:
            yield SseEvent(event_name or "message", "\n".join(data_lines))

    for line in lines:
        text = line.rstrip("\r\n")
        if text == "":
            yield from flush()
            event_name = ""
            data_lines = []
            continue
        if text.startswith(":"):
            continue  # 注释 / 心跳
        if ":" in text:
            field_name, _, value = text.partition(":")
            # 规范要求去掉一个前导空格；这里去除全部前导空白，对真实服务器的格式偏差更宽容（不影响 JSON 解析）。
            value = value.lstrip()
        else:
            field_name, value = text, ""
        if field_name == "event":
            event_name = value
        elif field_name == "data":
            data_lines.append(value)

    # 流提前结束时 flush 尾部未分隔的事件（宽容处理）
    yield from flush()


def is_done_marker(raw: str) -> bool:
    return raw.strip() == "[DONE]"
