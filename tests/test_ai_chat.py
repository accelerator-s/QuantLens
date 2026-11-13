"""Display reasoning separately without changing text-only API consumers."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from webui.backend.ai_chat import chat_once, chat_reply


class ChatReasoning(unittest.TestCase):
    def setUp(self):
        self.router = SimpleNamespace(request_timeout=20)
        self.model = SimpleNamespace(name="test")

    def test_deepseek_reasoning_is_returned_separately(self):
        with patch("webui.backend.ai_chat._api_key_for", return_value="test"), \
             patch("ai.transport.completion", return_value={"content": "**答案**", "reasoning_content": "检查历史数据"}):
            self.assertEqual(chat_reply(self.router, self.model, []), {"reply": "**答案**", "reasoning": "检查历史数据"})
            self.assertEqual(chat_once(self.router, self.model, []), "**答案**")

    def test_anthropic_thinking_blocks_are_displayed_but_not_signatures(self):
        message = {"content": "答案", "_anthropic_content": [
            {"type": "thinking", "thinking": "先检查范围", "signature": "private-signature"},
            {"type": "redacted_thinking", "data": "redacted-data"},
            {"type": "text", "text": "答案"},
        ]}
        with patch("webui.backend.ai_chat._api_key_for", return_value="test"), patch("ai.transport.completion", return_value=message):
            self.assertEqual(chat_reply(self.router, self.model, []), {"reply": "答案", "reasoning": "先检查范围"})

    def test_signature_tags_and_payload_are_removed_from_model_output(self):
        message = {
            "content": "<think>检查数据<signature>private\nsignature</signature></think>答案",
            "reasoning_content": "先检查范围<signature>private-signature</signature>，再验证<signature>other</signature>。",
        }
        with patch("webui.backend.ai_chat._api_key_for", return_value="test"), patch("ai.transport.completion", return_value=message):
            self.assertEqual(chat_reply(self.router, self.model, []), {
                "reply": "<think>检查数据</think>答案", "reasoning": "先检查范围，再验证。",
            })

    def test_unclosed_signature_is_removed_from_thinking_blocks(self):
        message = {"content": "答案", "_anthropic_content": [
            {"type": "thinking", "thinking": "先检查范围<signature>incomplete-signature"},
        ]}
        with patch("webui.backend.ai_chat._api_key_for", return_value="test"), patch("ai.transport.completion", return_value=message):
            self.assertEqual(chat_reply(self.router, self.model, []), {"reply": "答案", "reasoning": "先检查范围"})
