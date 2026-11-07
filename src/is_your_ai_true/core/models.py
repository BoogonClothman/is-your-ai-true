"""数据模型：端点配置、消息、请求参数。"""

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_OPENAI_CHAT = "openai_chat"
PROTOCOL_OPENAI_RESPONSES = "openai_responses"
PROTOCOL_ANTHROPIC = "anthropic"

PROTOCOL_LABELS: dict[str, str] = {
    PROTOCOL_OPENAI_CHAT: "OpenAI Chat Completions (/v1/chat/completions)",
    PROTOCOL_OPENAI_RESPONSES: "OpenAI Responses (/v1/responses)",
    PROTOCOL_ANTHROPIC: "Anthropic Messages (/v1/messages)",
}

DEFAULT_BASE_URLS: dict[str, str] = {
    PROTOCOL_OPENAI_CHAT: "https://api.openai.com/v1",
    PROTOCOL_OPENAI_RESPONSES: "https://api.openai.com/v1",
    PROTOCOL_ANTHROPIC: "https://api.anthropic.com",
}

DEFAULT_MODELS: dict[str, str] = {
    PROTOCOL_OPENAI_CHAT: "gpt-4o-mini",
    PROTOCOL_OPENAI_RESPONSES: "gpt-4o-mini",
    PROTOCOL_ANTHROPIC: "claude-sonnet-4-20250514",
}

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLES: tuple[str, ...] = (ROLE_SYSTEM, ROLE_USER, ROLE_ASSISTANT)


@dataclass
class ChatMessage:
    """单条对话消息。"""

    role: str = ROLE_USER
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class EndpointConfig:
    """端点连接配置。"""

    name: str = "默认端点"
    protocol: str = PROTOCOL_OPENAI_CHAT
    base_url: str = DEFAULT_BASE_URLS[PROTOCOL_OPENAI_CHAT]
    api_key: str = ""
    model: str = DEFAULT_MODELS[PROTOCOL_OPENAI_CHAT]
    timeout: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "timeout": self.timeout,
            "extra_headers": dict(self.extra_headers),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointConfig:
        return cls(
            name=data.get("name", "默认端点"),
            protocol=data.get("protocol", PROTOCOL_OPENAI_CHAT),
            base_url=data.get(
                "base_url", DEFAULT_BASE_URLS[PROTOCOL_OPENAI_CHAT]
            ),
            api_key=data.get("api_key", ""),
            model=data.get("model", DEFAULT_MODELS[PROTOCOL_OPENAI_CHAT]),
            timeout=float(data.get("timeout", 120.0)),
            extra_headers=dict(data.get("extra_headers", {})),
        )


@dataclass
class RequestParams:
    """采样参数。None 表示不发送给端点。"""

    temperature: float | None = 1.0
    max_tokens: int | None = 1024
    top_p: float | None = 1.0
    stream: bool = True
