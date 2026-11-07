"""回复记录：模型 ID 核验、指纹提取与完整结构备查。"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from .events import MetadataEvent, NonStreamResult
from .models import ChatMessage, EndpointConfig, RequestParams

STATUS_SUCCESS = "success"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"


def mask_api_key(key: str) -> str:
    """API Key 脱敏：只保留首尾少量字符，其余以 … 代替。"""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:3] + "…"
    return key[:3] + "…" + key[-4:]


@dataclass
class ResponseRecord:
    """一次请求-回复的完整留档。"""

    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    record_id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: str = STATUS_SUCCESS
    error: str = ""

    # ---- 请求快照 ----
    protocol: str = ""
    base_url: str = ""
    api_key_hint: str = ""
    requested_model: str = ""
    request_messages: list[dict[str, Any]] = field(default_factory=list)
    request_params: dict[str, Any] = field(default_factory=dict)
    request_body: dict[str, Any] | None = None

    # ---- 响应元数据（核验主体）----
    response_id: str | None = None
    reported_model: str | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None
    usage: dict[str, int] | None = None

    # ---- 内容 ----
    reasoning_text: str = ""
    output_text: str = ""

    # 完整原始结构：非流式为响应 payload；流式为 {"stream": true, "events": [...]}
    raw: Any = None

    @classmethod
    def from_request(
        cls,
        config: EndpointConfig,
        messages: list[ChatMessage],
        params: RequestParams,
    ) -> ResponseRecord:
        return cls(
            protocol=config.protocol,
            base_url=config.base_url,
            api_key_hint=mask_api_key(config.api_key),
            requested_model=config.model,
            request_messages=[m.to_dict() for m in messages],
            request_params={
                "temperature": params.temperature,
                "max_tokens": params.max_tokens,
                "top_p": params.top_p,
                "stream": params.stream,
            },
            raw={"stream": True, "events": []} if params.stream else None,
        )

    # ---- 填充 ----

    def append_event(self, name: str, data: dict[str, Any]) -> None:
        """记录一个流式事件的原始结构。"""
        events = self.raw["events"]
        events.append({"event": name, "data": data})

    def set_payload(self, payload: dict[str, Any]) -> None:
        """记录非流式响应的完整 payload。"""
        self.raw = payload

    def apply_metadata(self, event: MetadataEvent) -> None:
        """合并元数据事件；仅当字段非空时覆盖（流中可能多次上报）。"""
        if event.response_id:
            self.response_id = event.response_id
        if event.reported_model:
            self.reported_model = event.reported_model
        if event.system_fingerprint:
            self.system_fingerprint = event.system_fingerprint
        if event.service_tier:
            self.service_tier = event.service_tier

    def apply_non_stream(self, result: NonStreamResult) -> None:
        self.output_text = result.text
        self.reasoning_text = result.reasoning
        if result.response_id:
            self.response_id = result.response_id
        if result.reported_model:
            self.reported_model = result.reported_model
        if result.system_fingerprint:
            self.system_fingerprint = result.system_fingerprint
        if result.service_tier:
            self.service_tier = result.service_tier
        if result.usage is not None:
            self.usage = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            }

    def mark_failed(self, message: str) -> None:
        self.status = STATUS_FAILED
        self.error = message

    def set_metadata(
        self,
        reported_model: str | None = None,
        response_id: str | None = None,
        system_fingerprint: str | None = None,
        service_tier: str | None = None,
    ) -> None:
        """直接设置元数据（测试与非流式路径的便捷入口）。"""
        if reported_model:
            self.reported_model = reported_model
        if response_id:
            self.response_id = response_id
        if system_fingerprint:
            self.system_fingerprint = system_fingerprint
        if service_tier:
            self.service_tier = service_tier

    def mark_cancelled(self) -> None:
        self.status = STATUS_CANCELLED

    # ---- 核验 ----

    def model_match(self) -> bool | None:
        """请求模型与响应报告模型是否一致；响应未报告时为 None。"""
        if not self.reported_model:
            return None
        return self.reported_model.strip() == (self.requested_model or "").strip()

    # ---- 导出 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "error": self.error or None,
            "request": {
                "protocol": self.protocol,
                "base_url": self.base_url,
                "api_key_hint": self.api_key_hint,
                "requested_model": self.requested_model,
                "messages": self.request_messages,
                "params": self.request_params,
                "body": self.request_body,
            },
            "response": {
                "id": self.response_id,
                "reported_model": self.reported_model,
                "requested_model": self.requested_model,
                "model_match": self.model_match(),
                "system_fingerprint": self.system_fingerprint,
                "service_tier": self.service_tier,
                "usage": self.usage,
            },
            "content": {
                "reasoning": self.reasoning_text,
                "output": self.output_text,
            },
            "raw": self.raw,
        }

    def to_jsonl_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def summary(self, archive_path: str | None = None) -> dict[str, Any]:
        """供 UI 展示的核验摘要。"""
        return {
            "status": self.status,
            "requested_model": self.requested_model,
            "reported_model": self.reported_model or "",
            "model_match": self.model_match(),
            "response_id": self.response_id or "",
            "system_fingerprint": self.system_fingerprint or "",
            "service_tier": self.service_tier or "",
            "usage": self.usage or {},
            "archive_path": archive_path or "",
            "record_id": self.record_id,
        }
