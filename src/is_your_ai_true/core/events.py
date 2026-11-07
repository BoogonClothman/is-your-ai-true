"""统一流事件。

各协议 provider 把协议特定的 SSE 事件转换成这些中间表示，
上层（worker / UI）无需关心协议差异。
"""

from dataclasses import dataclass, field
from typing import Any

from .stats import Usage


@dataclass
class DeltaEvent:
    """一段增量正文文本。"""

    text: str


@dataclass
class ReasoningEvent:
    """一段增量思维链 / 推理摘要文本。"""

    text: str


@dataclass
class UsageEvent:
    """一次用量上报。数值以最后一次为准（部分协议会多次上报累计值）。"""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ErrorEvent:
    """协议层面的错误（例如 Responses API 的 response.failed）。"""

    message: str


@dataclass
class MetadataEvent:
    """从响应中提取的标识性元数据。

    - ``response_id``：本次回复的服务端 ID（chatcmpl-* / resp_* / msg_*）
    - ``reported_model``：响应**实际报告**的模型 ID，用于与请求模型核验
    - ``system_fingerprint``：服务商指纹（OpenAI 为 fp_*；Anthropic 不提供）
    - ``service_tier``：服务层级（OpenAI 为 scale/priority/default）
    """

    response_id: str | None = None
    reported_model: str | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None


StreamEvent = (
    DeltaEvent | ReasoningEvent | UsageEvent | ErrorEvent | MetadataEvent
)


@dataclass
class NonStreamResult:
    """非流式响应的解析结果。"""

    text: str = ""
    reasoning: str = ""
    usage: Usage | None = None
    response_id: str | None = None
    reported_model: str | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
