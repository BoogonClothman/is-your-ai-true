"""核心层单元测试：SSE 解析、三种协议适配（含思维链与元数据核验）、统计、归档。"""

import json
import tempfile
import unittest
from pathlib import Path

from is_your_ai_true.core.archive import Archive
from is_your_ai_true.core.events import (
    DeltaEvent,
    MetadataEvent,
    ReasoningEvent,
    UsageEvent,
)
from is_your_ai_true.core.models import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    ChatMessage,
    EndpointConfig,
    RequestParams,
)
from is_your_ai_true.core.providers import get_provider
from is_your_ai_true.core.record import ResponseRecord, mask_api_key
from is_your_ai_true.core.sse import is_done_marker, parse_sse_stream
from is_your_ai_true.core.stats import RequestStats, Usage


def _config(
    protocol: str,
    base: str = "https://api.openai.com/v1",
    key: str = "sk-test",
    model: str = "test-model",
) -> EndpointConfig:
    return EndpointConfig(
        protocol=protocol, base_url=base, api_key=key, model=model
    )


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="你是助手"),
        ChatMessage(role="user", content="你好"),
    ]


class SseParserTest(unittest.TestCase):
    def test_data_only_event(self) -> None:
        events = list(parse_sse_stream(['data: {"a": 1}', ""]))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "message")
        self.assertEqual(events[0].data, '{"a": 1}')

    def test_named_event_and_done_marker(self) -> None:
        lines = [
            "event: response.output_text.delta",
            'data: {"delta": "hi"}',
            "",
            "data: [DONE]",
            "",
        ]
        events = list(parse_sse_stream(lines))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].name, "response.output_text.delta")
        self.assertTrue(is_done_marker(events[1].data))

    def test_multiline_data_comment_and_tail_flush(self) -> None:
        lines = [
            ": keep-alive",
            "event: e",
            "data: line1",
            "data:line2",
            "",
            "data: tail-without-blank",
        ]
        events = list(parse_sse_stream(lines))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].name, "e")
        self.assertEqual(events[0].data, "line1\nline2")
        self.assertEqual(events[1].data, "tail-without-blank")

    def test_prefix_space_stripped(self) -> None:
        events = list(parse_sse_stream(['data:   {"b": 2}', ""]))
        self.assertEqual(events[0].data, '{"b": 2}')

    def test_blank_stream_yields_nothing(self) -> None:
        self.assertEqual(list(parse_sse_stream([])), [])


class OpenAIChatProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = get_provider(PROTOCOL_OPENAI_CHAT)

    def test_build_request(self) -> None:
        url, headers, body = self.provider.build_request(
            _config(PROTOCOL_OPENAI_CHAT), _messages(), RequestParams()
        )
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "你好"},
            ],
        )
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["temperature"], 1.0)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertEqual(body["top_p"], 1.0)

    def test_build_request_omits_none_params(self) -> None:
        _, _, body = self.provider.build_request(
            _config(PROTOCOL_OPENAI_CHAT),
            _messages(),
            RequestParams(temperature=None, max_tokens=None, top_p=None, stream=False),
        )
        self.assertNotIn("temperature", body)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("top_p", body)
        self.assertNotIn("stream_options", body)
        self.assertFalse(body["stream"])

    def test_stream_delta_reasoning_usage(self) -> None:
        chunk = {"choices": [{"index": 0, "delta": {"content": "你好"}}]}
        events = list(
            self.provider.iter_stream_events("chat.completion.chunk", chunk)
        )
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], DeltaEvent)
        self.assertEqual(events[0].text, "你好")

        reasoning_chunk = {
            "choices": [
                {"index": 0, "delta": {"reasoning_content": "先想一下"}}
            ]
        }
        events = list(
            self.provider.iter_stream_events("chat.completion.chunk", reasoning_chunk)
        )
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ReasoningEvent)
        self.assertEqual(events[0].text, "先想一下")

        usage_chunk = {
            "id": "chatcmpl-1",
            "model": "test-model",
            "system_fingerprint": "fp_abc",
            "service_tier": "default",
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        events = list(
            self.provider.iter_stream_events(
                "chat.completion.chunk", usage_chunk
            )
        )
        kinds = [type(e) for e in events]
        self.assertIn(UsageEvent, kinds)
        usage = events[kinds.index(UsageEvent)]
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.output_tokens, 5)
        metadata = events[kinds.index(MetadataEvent)]
        self.assertEqual(metadata.response_id, "chatcmpl-1")
        self.assertEqual(metadata.reported_model, "test-model")
        self.assertEqual(metadata.system_fingerprint, "fp_abc")
        self.assertEqual(metadata.service_tier, "default")

    def test_stream_error(self) -> None:
        events = list(
            self.provider.iter_stream_events(
                "message", {"error": {"message": "boom"}}
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].message, "boom")

    def test_parse_non_stream(self) -> None:
        payload = {
            "id": "chatcmpl-2",
            "model": "test-model",
            "system_fingerprint": "fp_xyz",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "嗨",
                        "reasoning_content": "想过了",
                    }
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2},
        }
        result = self.provider.parse_non_stream(payload)
        self.assertEqual(result.text, "嗨")
        self.assertEqual(result.reasoning, "想过了")
        self.assertEqual(result.response_id, "chatcmpl-2")
        self.assertEqual(result.reported_model, "test-model")
        self.assertEqual(result.system_fingerprint, "fp_xyz")
        self.assertIsNotNone(result.usage)
        assert result.usage is not None
        self.assertEqual(result.usage.total(), 10)
        self.assertEqual(result.raw, payload)


class OpenAIResponsesProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = get_provider(PROTOCOL_OPENAI_RESPONSES)

    def test_build_request(self) -> None:
        url, headers, body = self.provider.build_request(
            _config(PROTOCOL_OPENAI_RESPONSES), _messages(), RequestParams()
        )
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        # Responses 用 input 数组与 max_output_tokens
        self.assertEqual(
            body["input"],
            [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "你好"},
            ],
        )
        self.assertEqual(body["max_output_tokens"], 1024)
        self.assertNotIn("max_tokens", body)
        self.assertTrue(body["stream"])

    def test_stream_delta_reasoning_completed(self) -> None:
        delta = list(
            self.provider.iter_stream_events(
                "response.output_text.delta", {"delta": "你好"}
            )
        )
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0].text, "你好")

        reasoning = list(
            self.provider.iter_stream_events(
                "response.reasoning_summary_text.delta", {"delta": "推理摘要"}
            )
        )
        self.assertEqual(len(reasoning), 1)
        self.assertIsInstance(reasoning[0], ReasoningEvent)
        self.assertEqual(reasoning[0].text, "推理摘要")

        created = list(
            self.provider.iter_stream_events(
                "response.created",
                {"response": {"id": "resp_1", "model": "test-model"}},
            )
        )
        self.assertEqual(created[0].response_id, "resp_1")
        self.assertEqual(created[0].reported_model, "test-model")

        completed = list(
            self.provider.iter_stream_events(
                "response.completed",
                {
                    "response": {
                        "id": "resp_1",
                        "model": "test-model",
                        "system_fingerprint": "fp_qrs",
                        "usage": {"input_tokens": 4, "output_tokens": 6},
                    }
                },
            )
        )
        kinds = [type(e) for e in completed]
        self.assertIn(MetadataEvent, kinds)
        metadata = completed[kinds.index(MetadataEvent)]
        self.assertEqual(metadata.system_fingerprint, "fp_qrs")
        self.assertIn(UsageEvent, kinds)
        usage = completed[kinds.index(UsageEvent)]
        self.assertEqual(usage.input_tokens, 4)
        self.assertEqual(usage.output_tokens, 6)

        failed = list(
            self.provider.iter_stream_events(
                "response.failed", {"error": {"message": "rate limited"}}
            )
        )
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].message, "rate limited")

    def test_parse_non_stream(self) -> None:
        payload = {
            "id": "resp_2",
            "model": "test-model",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "考虑中"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "嗨"}],
                },
            ],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }
        result = self.provider.parse_non_stream(payload)
        self.assertEqual(result.text, "嗨")
        self.assertEqual(result.reasoning, "考虑中")
        self.assertEqual(result.response_id, "resp_2")
        self.assertEqual(result.reported_model, "test-model")
        assert result.usage is not None
        self.assertEqual(result.usage.total(), 5)


class AnthropicProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = get_provider(PROTOCOL_ANTHROPIC)

    def test_build_request_system_top_level(self) -> None:
        url, headers, body = self.provider.build_request(
            _config(PROTOCOL_ANTHROPIC, base="https://api.anthropic.com"),
            _messages(),
            RequestParams(max_tokens=None),
        )
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "sk-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        # system 必须置于顶层，且 max_tokens 必填（None 时给默认值）
        self.assertEqual(body["system"], "你是助手")
        self.assertEqual(body["messages"], [{"role": "user", "content": "你好"}])
        self.assertEqual(body["max_tokens"], 4096)

    def test_base_url_with_v1_suffix(self) -> None:
        url, _, body = self.provider.build_request(
            _config(PROTOCOL_ANTHROPIC, base="https://api.anthropic.com/v1"),
            [ChatMessage(role="user", content="hi")],
            RequestParams(),
        )
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertNotIn("system", body)

    def test_stream_events(self) -> None:
        delta = list(
            self.provider.iter_stream_events(
                "content_block_delta",
                {"delta": {"type": "text_delta", "text": "嗨"}},
            )
        )
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0].text, "嗨")

        thinking = list(
            self.provider.iter_stream_events(
                "content_block_delta",
                {"delta": {"type": "thinking_delta", "thinking": "思考中"}},
            )
        )
        self.assertEqual(len(thinking), 1)
        self.assertIsInstance(thinking[0], ReasoningEvent)
        self.assertEqual(thinking[0].text, "思考中")

        start = list(
            self.provider.iter_stream_events(
                "message_start",
                {"message": {"id": "msg_9", "model": "test-model"}},
            )
        )
        kinds = [type(e) for e in start]
        metadata = start[kinds.index(MetadataEvent)]
        self.assertEqual(metadata.response_id, "msg_9")
        self.assertEqual(metadata.reported_model, "test-model")
        self.assertIsNone(metadata.system_fingerprint)  # Anthropic 无指纹

        update = list(
            self.provider.iter_stream_events(
                "message_delta", {"usage": {"output_tokens": 12}}
            )
        )
        kinds = [type(e) for e in update]
        self.assertIn(UsageEvent, kinds)
        self.assertEqual(update[kinds.index(UsageEvent)].output_tokens, 12)

        error = list(
            self.provider.iter_stream_events(
                "error", {"error": {"message": "invalid api key"}}
            )
        )
        self.assertEqual(error[0].message, "invalid api key")

    def test_parse_non_stream(self) -> None:
        payload = {
            "id": "msg_2",
            "model": "test-model",
            "content": [
                {"type": "thinking", "thinking": "想了想"},
                {"type": "text", "text": "嗨"},
            ],
            "usage": {"input_tokens": 6, "output_tokens": 4},
        }
        result = self.provider.parse_non_stream(payload)
        self.assertEqual(result.text, "嗨")
        self.assertEqual(result.reasoning, "想了想")
        self.assertEqual(result.response_id, "msg_2")
        self.assertEqual(result.reported_model, "test-model")
        self.assertIsNone(result.system_fingerprint)
        assert result.usage is not None
        self.assertEqual(result.usage.output_tokens, 4)

    def test_unsupported_protocol_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_provider("nope")


class StatsTest(unittest.TestCase):
    def test_metrics(self) -> None:
        stats = RequestStats()
        stats.mark_started()
        stats.mark_first_token()
        stats.record_delta("一二三四" * 10)  # 40 字符 → 估算 10 tokens
        stats.mark_finished()
        self.assertIsNotNone(stats.ttft)
        self.assertGreaterEqual(stats.ttft, 0)
        self.assertGreaterEqual(stats.total_elapsed, 0)
        self.assertEqual(stats.output_tokens_count, 10)
        self.assertGreater(stats.tokens_per_second, 0)
        rendered = stats.to_dict()
        self.assertIn("发起时间", rendered)
        self.assertNotEqual(rendered["吞吐"], "--")

    def test_usage_overrides_estimation(self) -> None:
        stats = RequestStats()
        stats.mark_started()
        stats.usage = Usage(input_tokens=3, output_tokens=7)
        self.assertEqual(stats.output_tokens_count, 7)

    def test_missing_first_token_gives_none_metrics(self) -> None:
        stats = RequestStats()
        stats.mark_started()
        stats.mark_finished()
        self.assertIsNone(stats.ttft)
        self.assertIsNone(stats.tokens_per_second)


class RecordTest(unittest.TestCase):
    def _record(self, requested: str = "m-req", reported: str | None = "m-req"):
        record = ResponseRecord.from_request(
            _config(PROTOCOL_OPENAI_CHAT, model=requested),
            _messages(),
            RequestParams(),
        )
        record.append_event("chat.completion.chunk", {"id": "1"})
        record.set_metadata(reported)
        return record

    def test_from_request_snapshot_and_masking(self) -> None:
        record = self._record()
        data = record.to_dict()
        self.assertEqual(data["request"]["requested_model"], "m-req")
        self.assertEqual(data["request"]["api_key_hint"], "sk-…")
        self.assertEqual(len(data["request"]["messages"]), 2)
        # 流式：raw 为事件列表
        self.assertEqual(data["raw"]["stream"], True)
        self.assertEqual(len(data["raw"]["events"]), 1)

    def test_model_match(self) -> None:
        self.assertTrue(self._record("gpt-4o", "gpt-4o").model_match())
        self.assertFalse(self._record("gpt-4o", "gpt-4o-mini").model_match())
        self.assertIsNone(self._record("gpt-4o", None).model_match())

    def test_model_match_ignores_whitespace(self) -> None:
        self.assertTrue(self._record("gpt-4o", "  gpt-4o  ").model_match())

    def test_mask_api_key(self) -> None:
        self.assertEqual(mask_api_key(""), "")
        self.assertEqual(mask_api_key("sk-123"), "sk-…")
        self.assertEqual(mask_api_key("sk-abcdefghij"), "sk-…ghij")

    def test_summary(self) -> None:
        record = self._record("gpt-4o", "gpt-4o-mini")
        record.response_id = "chatcmpl-7"
        record.system_fingerprint = "fp_z"
        record.service_tier = "default"
        summary = record.summary("/tmp/responses.jsonl")
        self.assertIs(summary["model_match"], False)
        self.assertEqual(summary["reported_model"], "gpt-4o-mini")
        self.assertEqual(summary["response_id"], "chatcmpl-7")
        self.assertEqual(summary["system_fingerprint"], "fp_z")
        self.assertEqual(summary["archive_path"], "/tmp/responses.jsonl")


class ArchiveTest(unittest.TestCase):
    def test_append_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Archive(Path(tmp) / "records")
            first = self._record("_a", "_a")
            second = self._record("_b", "_c")
            path = archive.append(first)
            archive.append(second)
            self.assertEqual(archive.count(), 2)
            self.assertEqual(path.name, "responses.jsonl")
            self.assertTrue(path.exists())

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first_back = json.loads(lines[0])
            self.assertEqual(first_back["request"]["requested_model"], "_a")
            self.assertIs(first_back["response"]["model_match"], True)
            second_back = json.loads(lines[1])
            self.assertIs(second_back["response"]["model_match"], False)

    def _record(self, requested: str, reported: str | None) -> ResponseRecord:
        record = ResponseRecord.from_request(
            _config(PROTOCOL_OPENAI_CHAT, model=requested),
            _messages(),
            RequestParams(),
        )
        record.set_metadata(reported)
        return record


if __name__ == "__main__":
    unittest.main()
