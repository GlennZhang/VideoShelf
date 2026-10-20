"""Tests for OmlxClient helpers that need no live server."""

from src.library.omlx_client import (
    OmlxError,
    clean_transcript,
    extract_json,
    normalize_base_url,
)


class TestNormalizeBaseUrl:
    def test_appends_v1(self):
        assert normalize_base_url("http://127.0.0.1:8989") == "http://127.0.0.1:8989/v1"

    def test_keeps_v1_and_strips_trailing_slash(self):
        assert normalize_base_url("https://x.cn/llm/v1/") == "https://x.cn/llm/v1"


class TestCleanTranscript:
    def test_strips_ollama_qwen3_asr_wrapper(self):
        text = "language Chinese<asr_text>今天我们学习国画。</asr_text>"
        assert clean_transcript(text) == "今天我们学习国画。"

    def test_wrapper_without_closing_tag(self):
        text = "language Chinese<asr_text>今天我们学习国画。"
        assert clean_transcript(text) == "今天我们学习国画。"

    def test_plain_text_untouched(self):
        assert clean_transcript("  你好。  ") == "你好。"

    def test_empty_string(self):
        assert clean_transcript("") == ""


class TestExtractJson:
    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_prose(self):
        assert extract_json('结果如下 {"a": {"b": 2}} 请查收') == {"a": {"b": 2}}

    def test_no_json_raises(self):
        try:
            extract_json("no json here")
            raise AssertionError("expected OmlxError")
        except OmlxError:
            pass
