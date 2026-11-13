"""File persistence and conflict handling for chat history."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from webui.backend.routes import chat_history


class ChatHistory(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"QUANTLENS_HOME": self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        app = FastAPI()
        app.include_router(chat_history.router, prefix="/api")
        self.client = TestClient(app)
        self.chat_id = str(uuid4())
        self.url = f"/api/chat-history/{self.chat_id}"
        self.payload = {"scope": "factor-research", "messages": [{"role": "user", "content": "研究盈利改善"}],
                        "draft": "观察一个月", "metadata": {"model": "test-model"}}

    def test_json_document_survives_new_client_and_preserves_context(self):
        response = self.client.put(self.url, json=self.payload)
        self.assertEqual(response.status_code, 200)
        path = Path(self.directory.name) / "data/chat_history" / f"{self.chat_id}.json"
        document = json.loads(path.read_text())
        self.assertEqual(document["messages"], self.payload["messages"])
        self.assertEqual(document["draft"], self.payload["draft"])
        self.assertEqual(document["metadata"], self.payload["metadata"])
        other = TestClient(self.client.app)
        self.assertEqual(other.get("/api/chat-history", params={"scope": "factor-research"}).json()["items"], [document])
        self.assertEqual(other.get("/api/chat-history", params={"scope": "strategy:new"}).json()["items"], [])

    def test_empty_conversation_is_saved_as_a_file(self):
        response = self.client.put(self.url, json={**self.payload, "messages": [], "draft": ""})
        self.assertEqual(response.status_code, 200)
        path = Path(self.directory.name) / "data/chat_history" / f"{self.chat_id}.json"
        self.assertTrue(path.is_file())
        document = self.client.get("/api/chat-history", params={"scope": self.payload["scope"]}).json()["items"][0]
        self.assertEqual(document["messages"], [])
        self.assertEqual(document["draft"], "")

    def test_late_write_cannot_recreate_deleted_document(self):
        result = self.client.put(self.url, json=self.payload).json()
        self.assertEqual(self.client.delete(self.url).status_code, 200)
        response = self.client.put(self.url, json={**self.payload, "revision": result["revision"]})
        self.assertEqual(response.status_code, 409)
        self.assertFalse((Path(self.directory.name) / "data/chat_history" / f"{self.chat_id}.json").exists())

    def test_stale_revision_and_wrong_scope_do_not_overwrite(self):
        self.client.put(self.url, json=self.payload)
        updated = {**self.payload, "draft": "新草稿", "revision": 1}
        self.assertEqual(self.client.put(self.url, json=updated).status_code, 200)
        self.assertEqual(self.client.put(self.url, json=self.payload).status_code, 409)
        self.assertEqual(self.client.put(self.url, json={**updated, "scope": "strategy:new", "revision": 2}).status_code, 409)
        self.assertEqual(self.client.get("/api/chat-history", params={"scope": "factor-research"}).json()["items"][0]["draft"], "新草稿")

    def test_invalid_identifier_and_message_are_rejected(self):
        self.assertEqual(self.client.put("/api/chat-history/not-a-uuid", json=self.payload).status_code, 422)
        self.assertEqual(self.client.put(self.url, json={**self.payload, "messages": [{"role": "system", "content": "test"}]}).status_code, 422)

    def test_reasoning_and_manual_title_survive_reloading(self):
        payload = {**self.payload, "title": "财报后的收益变化", "title_manual": True,
                   "messages": [*self.payload["messages"], {"role": "assistant", "content": "**结论**：继续检验。", "reasoning": "先确认财报时间。"}]}
        self.assertEqual(self.client.put(self.url, json=payload).status_code, 200)
        document = self.client.get("/api/chat-history", params={"scope": payload["scope"]}).json()["items"][0]
        self.assertEqual(document["title"], payload["title"])
        self.assertTrue(document["title_manual"])
        self.assertEqual(document["messages"][-1]["reasoning"], payload["messages"][-1]["reasoning"])

    def test_title_only_change_does_not_reorder_history(self):
        first = self.client.put(self.url, json=self.payload).json()
        with patch.object(chat_history.time, "time", return_value=9999999999):
            changed = self.client.put(self.url, json={**self.payload, "revision": 1, "title": "新标题", "title_manual": True}).json()
        self.assertEqual(changed["updatedAt"], first["updatedAt"])

    def test_title_generation_uses_selected_model_and_only_returns_title(self):
        with patch("ai.router.AIRouter"), patch("webui.backend.ai_chat.pick_chat_model") as choose, \
             patch("webui.backend.ai_chat.chat_once", return_value="“财报后的收益变化”") as reply:
            response = self.client.post("/api/chat-history/title", json={"model": "selected-model", "messages": self.payload["messages"]})
        self.assertEqual(response.json(), {"title": "财报后的收益变化"})
        self.assertEqual(choose.call_args.args[1], "selected-model")
        self.assertIn("研究盈利改善", reply.call_args.args[2][-1]["content"])

    def test_failed_atomic_replace_preserves_existing_file(self):
        original = self.client.put(self.url, json=self.payload).json()
        with patch.object(chat_history.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.client.put(self.url, json={**self.payload, "revision": 1, "draft": "incomplete"})
        folder = Path(self.directory.name) / "data/chat_history"
        self.assertEqual(json.loads((folder / f"{self.chat_id}.json").read_text()), original)
        self.assertEqual(list(folder.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
