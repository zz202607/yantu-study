"""AI Q&A: grounded context, cache, quota and citation checks.

The paid provider is never called here.  The OCR and chat functions are
injected, and clause lookup is patched, so CI needs neither RapidOCR nor the
local standards library.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import qa  # noqa: E402
from app.store import Store  # noqa: E402

CONTEXT = {
    "qid": "C3-11",
    "question": "某题题干",
    "answer": "某题解析",
    "hints": [{
        "code": "JGJ 94-2008",
        "title": "建筑桩基技术规范",
        "clauses": ["5.4.3"],
        "pdf_page": 30,
        "source_label": "解析依据",
    }],
    "clauses": [{
        "code": "JGJ 94-2008",
        "title": "建筑桩基技术规范",
        "clauses": ["5.4.3"],
        "pdf_page": 30,
        "exact": True,
        "text": "中性点以上侧阻力取零。",
    }],
    "hash": "ctx-hash",
}

CONFIG = {
    "provider": "deepseek",
    "base_url": "https://example.invalid/v1",
    "model": "test-model",
    "api_key": "sk-test",
    "daily_limit": 2,
    "max_tokens": 800,
    "timeout": 30,
}


class QAStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qa-")
        self.store = Store(self.tmp)
        self.now = datetime.now().astimezone()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_has_qa_tables(self):
        conn = self.store._connect()
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " AND name LIKE 'qa_%'"
                )
            }
        finally:
            conn.close()
        self.assertEqual(version, "12")
        self.assertEqual(tables, {"qa_threads", "qa_messages"})

    def test_exchange_history_cache_and_quota(self):
        self.store.qa_add_exchange(
            "C3-11", "问题一", "回答一", sources={"hints": []},
            cache_key="k1", model="m", prompt_tokens=10,
            completion_tokens=5, now=self.now,
        )
        history = self.store.qa_history("C3-11")
        self.assertEqual([row["role"] for row in history], ["user", "assistant"])
        self.assertEqual(history[1]["sources"], {"hints": []})
        self.assertEqual(self.store.qa_calls_today(now=self.now), 1)
        hit = self.store.qa_cached("k1", now=self.now)
        self.assertEqual(hit["content"], "回答一")

        self.store.qa_add_exchange(
            "C3-11", "问题一（再问）", "回答一", sources={},
            cache_key="k1", cached=True, now=self.now,
        )
        self.assertEqual(self.store.qa_calls_today(now=self.now), 1)
        self.assertEqual(len(self.store.qa_history("C3-11")), 4)

        later = self.now + timedelta(days=31)
        self.assertIsNone(self.store.qa_cached("k1", days=30, now=later))
        self.assertTrue(self.store.qa_clear("C3-11"))
        self.assertEqual(self.store.qa_history("C3-11"), [])

    def test_config_is_local_and_public_view_hides_key(self):
        path = Path(self.tmp) / "llm.json"
        path.write_text(json.dumps(CONFIG), encoding="utf-8")
        config = qa.load_config(self.tmp)
        self.assertTrue(qa.configured(config))
        self.assertEqual(config["daily_limit"], 2)
        public = qa.public_config(config)
        self.assertNotIn("api_key", public)
        self.assertNotIn("sk-test", json.dumps(public))

    def test_missing_config_is_not_configured(self):
        config = qa.load_config(self.tmp)
        self.assertFalse(qa.configured(config))


class QAContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qa-ctx-")
        self.q = Path(self.tmp) / "q.png"
        self.a = Path(self.tmp) / "a.png"
        self.q.write_bytes(b"question")
        self.a.write_bytes(b"answer")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_context_caches_by_crop_signature(self):
        calls = []

        def ocr(path):
            calls.append(Path(path).name)
            return "题目文字" if Path(path).name == "q.png" else "解析文字"

        ctx = qa.build_context(
            "C3-11", self.q, self.a, cache_dir=self.tmp,
            hints=[], ocr_fn=ocr,
        )
        self.assertEqual(ctx["question"], "题目文字")
        self.assertEqual(ctx["answer"], "解析文字")
        self.assertEqual(len(calls), 2)
        qa.build_context(
            "C3-11", self.q, self.a, cache_dir=self.tmp,
            hints=[], ocr_fn=ocr,
        )
        self.assertEqual(len(calls), 2)  # cache hit, no second OCR
        self.a.write_bytes(b"answer changed")
        qa.build_context(
            "C3-11", self.q, self.a, cache_dir=self.tmp,
            hints=[], ocr_fn=ocr,
        )
        self.assertEqual(len(calls), 4)  # crop changed -> rebuild

    def test_messages_carry_context_history_and_question(self):
        messages = qa.build_messages(
            CONTEXT, "为什么中性点以上不计？",
            [{"role": "user", "content": "上一问"},
             {"role": "assistant", "content": "上一答"}],
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("某题题干", messages[0]["content"])
        self.assertIn("中性点以上侧阻力取零", messages[0]["content"])
        self.assertEqual(messages[-1],
                         {"role": "user", "content": "为什么中性点以上不计？"})
        self.assertEqual([row["role"] for row in messages[1:-1]],
                         ["user", "assistant"])

    def test_cache_key_ignores_history_but_tracks_question(self):
        one = qa.answer_cache_key(CONTEXT, "问题", "m")
        two = qa.answer_cache_key(CONTEXT, "问题", "m")
        other = qa.answer_cache_key(CONTEXT, "另一个问题", "m")
        self.assertEqual(one, two)
        self.assertNotEqual(one, other)


class QACitationTest(unittest.TestCase):
    def test_known_and_unknown_citations(self):
        def locate(code, clause):
            if clause == "5.4.3":
                return {"code": "JGJ 94-2008", "title": "建筑桩基技术规范",
                        "pdf_page": 30, "section_title": "负摩阻力"}
            return None

        with patch.object(qa.standards, "match_standard",
                          return_value={"code": "JGJ 94-2008",
                                        "title": "建筑桩基技术规范"}), \
             patch.object(qa.standards, "locate_clause", side_effect=locate):
            cited, warnings = qa.verify_citations(
                "依据《建筑桩基技术规范》JGJ 94-2008 第5.4.3条；"
                "另见《不存在的规范》第9.9.9条。", CONTEXT["hints"])
        self.assertEqual(cited[0]["clause"], "5.4.3")
        self.assertTrue(cited[0]["in_hints"])
        self.assertTrue(warnings)


class QAAskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qa-ask-")
        self.store = Store(self.tmp)
        self.now = datetime.now().astimezone()
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_chat(self, config, messages):
        self.calls.append(messages)
        return ("先给结论。\n依据：《建筑桩基技术规范》JGJ 94-2008 第5.4.3条。",
                {"prompt_tokens": 100, "completion_tokens": 20,
                 "total_tokens": 120})

    def test_ask_counts_once_and_cached_ask_is_free(self):
        with patch.object(qa.standards, "match_standard",
                          return_value={"code": "JGJ 94-2008",
                                        "title": "建筑桩基技术规范"}), \
             patch.object(qa.standards, "locate_clause",
                          return_value={"code": "JGJ 94-2008",
                                        "title": "建筑桩基技术规范",
                                        "pdf_page": 30,
                                        "section_title": "负摩阻力"}):
            first = qa.ask(self.store, "C3-11", "为什么？", CONTEXT, CONFIG,
                           chat_fn=self.fake_chat, now=self.now)
            second = qa.ask(self.store, "C3-11", "为什么？", CONTEXT, CONFIG,
                            chat_fn=self.fake_chat, now=self.now)
            third = qa.ask(self.store, "C3-11", "另一个问题？", CONTEXT, CONFIG,
                           chat_fn=self.fake_chat, now=self.now)
            with self.assertRaises(qa.QALimit):
                qa.ask(self.store, "C3-11", "额度用完后再问？", CONTEXT, CONFIG,
                       chat_fn=self.fake_chat, now=self.now)

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(self.calls), 2)  # cached ask made no API call
        self.assertEqual(first["quota"], {"used": 1, "limit": 2})
        self.assertEqual(third["quota"], {"used": 2, "limit": 2})
        self.assertEqual(len(self.store.qa_history("C3-11")), 6)
        self.assertEqual(self.store.qa_calls_today(now=self.now), 2)
        self.assertIn("5.4.3", [row["clause"] for row in first["sources"]["cited"]])

    def test_not_configured_and_long_question_are_rejected(self):
        with self.assertRaises(qa.QANotConfigured):
            qa.ask(self.store, "C3-11", "问题", CONTEXT,
                   {**CONFIG, "api_key": ""}, chat_fn=self.fake_chat)
        with self.assertRaises(qa.QAError):
            qa.ask(self.store, "C3-11", "长" * 600, CONTEXT, CONFIG,
                   chat_fn=self.fake_chat)


if __name__ == "__main__":
    unittest.main()
