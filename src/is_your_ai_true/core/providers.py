"""协议适配层。

把三种 API 协议（OpenAI Chat Completions / OpenAI Responses /
Anthropic Messages）映射到统一的请求构造与事件模型，
上层只需面对统一的 StreamEvent。

除正文与思维链增量外，每个 provider 还负责从响应中提取
标识性元数据（response id / 实际报告的模型 / system_fingerprint /
service_tier），供"模型 ID 核验与存档"使用。
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from .events import (
    DeltaEvent,
    ErrorEvent,
    MetadataEvent,
    NonStreamResult,
    ReasoningEvent,
    StreamEvent,
    UsageEvent,
)
from .models import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    ROLE_SYSTEM,
    ChatMessage,
    EndpointConfig,
    RequestParams,
)
from .stats import Usage

ANTHROPIC_API_VERSION = "2023-06-01"


class ProtocolProvider(ABC):
    """协议适配器接口。"""

    protocol: str

    @abstractmethod
    def build_request(
        self,
        config: EndpointConfig,
        messages: list[ChatMessage],
        params: RequestParams,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """构造请求，返回 (url, headers, json body)。"""

    @abstractmethod
    def iter_stream_events(
        self, name: str, data: dict[str, Any]
    ) -> Iterator[StreamEvent]:
        """把一个已解析的 SSE 事件转换为统一事件序列。"""

    @abstractmethod
    def parse_non_stream(
        self, payload: dict[str, Any]
    ) -> NonStreamResult:
        """解析非流式响应。"""


def _join_url(base_url: str, *segments: str) -> str:
    base = base_url.rstrip("/")
    parts = [seg.strip("/") for seg in segments if seg.strip("/")]
    return "/".join([base, *parts]) if parts else base


def _extract_texts(content: Any) -> str:
    """从 content 结构提取文本，兼容字符串与块列表两种形态。"""
    if isinstance(content, str):
        return content
    texts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return "".join(texts)


def _nonnull(**kwargs: Any) -> dict[str, Any]:
    """收集非空字段，用于构造 MetadataEvent。"""
    return {key: value for key, value in kwargs.items() if value}


class OpenAIChatProvider(ProtocolProvider):
    """OpenAI Chat Completions（/v1/chat/completions），兼容绝大多数中转网关。

    思维链位于 ``delta.reasoning_content``（官方 reasoning 模型）或
    ``delta.reasoning``（部分兼容网关）。
    """

    protocol = PROTOCOL_OPENAI_CHAT

    def build_request(self, config, messages, params):
        url = _join_url(config.base_url, "chat/completions")
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            **config.extra_headers,
        }
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "stream": bool(params.stream),
        }
        if params.temperature is not None:
            body["temperature"] = params.temperature
        if params.max_tokens is not None:
            body["max_tokens"] = params.max_tokens
        if params.top_p is not None:
            body["top_p"] = params.top_p
        if params.stream:
            # stream_options.include_usage 让最后一个 chunk 带上 usage
            body["stream_options"] = {"include_usage": True}
        return url, headers, body

    def iter_stream_events(self, name, data):
        choices = data.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                yield ReasoningEvent(reasoning)
            text = delta.get("content")
            if text:
                yield DeltaEvent(text)
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            yield UsageEvent(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            yield ErrorEvent(str(error["message"]))
        metadata = _nonnull(
            response_id=data.get("id"),
            reported_model=data.get("model"),
            system_fingerprint=data.get("system_fingerprint"),
            service_tier=data.get("service_tier"),
        )
        if metadata:
            yield MetadataEvent(**metadata)

    def parse_non_stream(self, payload):
        result = NonStreamResult(
            response_id=payload.get("id"),
            reported_model=payload.get("model"),
            system_fingerprint=payload.get("system_fingerprint"),
            service_tier=payload.get("service_tier"),
            raw=payload,
        )
        choices = payload.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            result.text = _extract_texts(message.get("content"))
            result.reasoning = _extract_texts(
                message.get("reasoning_content") or message.get("reasoning")
            )
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            result.usage = Usage(
                input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
            )
        return result


class OpenAIResponsesProvider(ProtocolProvider):
    """OpenAI Responses（/v1/responses）。

    SSE 为命名事件；usage 字段为 input_tokens / output_tokens。
    思维链摘要走 ``response.reasoning_summary_text.delta``。
    """

    protocol = PROTOCOL_OPENAI_RESPONSES

    def build_request(self, config, messages, params):
        url = _join_url(config.base_url, "responses")
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            **config.extra_headers,
        }
        body: dict[str, Any] = {
            "model": config.model,
            "input": [m.to_dict() for m in messages],
            "stream": bool(params.stream),
        }
        if params.temperature is not None:
            body["temperature"] = params.temperature
        if params.top_p is not None:
            body["top_p"] = params.top_p
        if params.max_tokens is not None:
            # Responses API 用 max_output_tokens
            body["max_output_tokens"] = params.max_tokens
        return url, headers, body

    def iter_stream_events(self, name, data):
        if name == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                yield DeltaEvent(delta)
        elif name == "response.reasoning_summary_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                yield ReasoningEvent(delta)
        elif name in ("response.created", "response.in_progress", "response.completed"):
            response = data.get("response") or {}
            metadata = _nonnull(
                response_id=response.get("id"),
                reported_model=response.get("model"),
                system_fingerprint=response.get("system_fingerprint"),
                service_tier=response.get("service_tier"),
            )
            if metadata:
                yield MetadataEvent(**metadata)
            if name == "response.completed":
                raw_usage = response.get("usage") or data.get("usage") or {}
                if isinstance(raw_usage, dict) and raw_usage:
                    yield UsageEvent(
                        input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
                        output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
                    )
        elif name in ("response.failed", "response.error", "error"):
            error = data.get("error") or (data.get("response") or {}).get("error")
            message = ""
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            elif isinstance(error, str):
                message = error
            if message:
                yield ErrorEvent(message)

    def parse_non_stream(self, payload):
        result = NonStreamResult(
            response_id=payload.get("id"),
            reported_model=payload.get("model"),
            system_fingerprint=payload.get("system_fingerprint"),
            service_tier=payload.get("service_tier"),
            raw=payload,
        )
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                result.reasoning += _extract_texts(item.get("summary"))
            elif item.get("type") == "message":
                result.text += _extract_texts(item.get("content"))
            elif isinstance(item.get("text"), str):
                result.text += item["text"]
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            result.usage = Usage(
                input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
                output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
            )
        return result


class AnthropicMessagesProvider(ProtocolProvider):
    """Anthropic Messages（/v1/messages）。

    system 消息须置于顶层，且 max_tokens 为必填参数。
    思维链是 ``thinking`` 类型的 content block（thinking_delta 事件）。
    Anthropic 不提供 system_fingerprint。
    """

    protocol = PROTOCOL_ANTHROPIC

    def build_request(self, config, messages, params):
        base = config.base_url.rstrip("/")
        url = (
            f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        )
        headers = {
            "x-api-key": config.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
            **config.extra_headers,
        }
        system_contents = [
            m.content for m in messages if m.role == ROLE_SYSTEM and m.content
        ]
        chat_messages = [m for m in messages if m.role != ROLE_SYSTEM]
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [m.to_dict() for m in chat_messages],
            # Anthropic 要求必填 max_tokens
            "max_tokens": (
                params.max_tokens if params.max_tokens is not None else 4096
            ),
            "stream": bool(params.stream),
        }
        if system_contents:
            body["system"] = "\n\n".join(system_contents)
        if params.temperature is not None:
            body["temperature"] = params.temperature
        if params.top_p is not None:
            body["top_p"] = params.top_p
        return url, headers, body

    def iter_stream_events(self, name, data):
        if name == "content_block_delta":
            delta = data.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = delta.get("text")
                if text:
                    yield DeltaEvent(text)
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking")
                if thinking:
                    yield ReasoningEvent(thinking)
        elif name in ("message_start", "message_delta"):
            source = data.get("message") if name == "message_start" else {}
            metadata = _nonnull(
                response_id=source.get("id") or data.get("id"),
                reported_model=source.get("model") or data.get("model"),
            )
            if metadata:
                yield MetadataEvent(**metadata)
            raw_usage = (
                (data.get("message") or {}).get("usage")
                if name == "message_start"
                else data.get("usage")
            ) or {}
            if isinstance(raw_usage, dict) and raw_usage:
                yield UsageEvent(
                    input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
                    output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
                )
        elif name == "error":
            error = data.get("error") or {}
            message = str(error.get("message") or error)
            if message:
                yield ErrorEvent(message)

    def parse_non_stream(self, payload):
        result = NonStreamResult(
            response_id=payload.get("id"),
            reported_model=payload.get("model"),
            raw=payload,
        )
        for block in payload.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "thinking":
                result.reasoning += block.get("thinking") or ""
            elif block_type == "text":
                result.text += block.get("text") or ""
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            result.usage = Usage(
                input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
                output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
            )
        return result


_PROVIDERS: dict[str, ProtocolProvider] = {
    PROTOCOL_OPENAI_CHAT: OpenAIChatProvider(),
    PROTOCOL_OPENAI_RESPONSES: OpenAIResponsesProvider(),
    PROTOCOL_ANTHROPIC: AnthropicMessagesProvider(),
}


def get_provider(protocol: str) -> ProtocolProvider:
    provider = _PROVIDERS.get(protocol)
    if provider is None:
        raise ValueError(f"不支持的协议：{protocol}")
    return provider
