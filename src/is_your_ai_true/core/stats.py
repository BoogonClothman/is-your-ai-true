"""请求性能与用量统计。"""

import time
from dataclasses import dataclass

CHARS_PER_TOKEN = 4.0  # 无 usage 时的粗略估算系数


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class RequestStats:
    """一次请求的计时与计量。时间戳来自 perf_counter，仅用于计算耗时。"""

    started_at: float = 0.0
    first_token_at: float | None = None
    finished_at: float | None = None
    usage: Usage | None = None
    output_chars: int = 0
    timestamp: float = 0.0  # 请求发起的墙钟时间，用于 UI 展示

    def mark_started(self) -> None:
        self.started_at = time.perf_counter()
        self.timestamp = time.time()

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()

    def mark_finished(self) -> None:
        self.finished_at = time.perf_counter()

    def record_delta(self, text: str) -> None:
        self.output_chars += len(text)

    @property
    def ttft(self) -> float | None:
        """首 token 耗时（毫秒）。"""
        if self.first_token_at is None:
            return None
        return (self.first_token_at - self.started_at) * 1000.0

    @property
    def total_elapsed(self) -> float | None:
        """请求总耗时（毫秒）。"""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at) * 1000.0

    @property
    def generation_elapsed(self) -> float | None:
        """生成阶段耗时（首 token → 结束，毫秒）。"""
        if self.first_token_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.first_token_at) * 1000.0

    @property
    def output_tokens_count(self) -> int:
        """输出 token 数：优先 usage，否则按字符粗估。"""
        if self.usage is not None and self.usage.output_tokens > 0:
            return self.usage.output_tokens
        return int(self.output_chars / CHARS_PER_TOKEN)

    @property
    def tokens_per_second(self) -> float | None:
        """生成吞吐（tokens/s），基于首 token 之后的时长。"""
        gen = self.generation_elapsed
        if gen is None or gen <= 0:
            return None
        return self.output_tokens_count / (gen / 1000.0)

    def to_dict(self) -> dict[str, str]:
        """供 UI 展示的格式化结果。"""
        def ms(value: float | None) -> str:
            return "--" if value is None else f"{value:.0f} ms"

        def rate(value: float | None) -> str:
            return "--" if value is None else f"{value:.1f} tok/s"

        usage_text = "--"
        if self.usage is not None:
            usage_text = (
                f"输入 {self.usage.input_tokens} / "
                f"输出 {self.usage.output_tokens} / "
                f"合计 {self.usage.total()}"
            )

        return {
            "发起时间": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "首 token (TTFT)": ms(self.ttft),
            "总耗时": ms(self.total_elapsed),
            "生成耗时": ms(self.generation_elapsed),
            "吞吐": rate(self.tokens_per_second),
            "输出文本": f"{self.output_chars} 字符",
            "用量 (tokens)": usage_text,
        }
