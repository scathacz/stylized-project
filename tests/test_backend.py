import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

from desk import db, expand
from desk.ai import classify_batch, validate_conclusion
from desk.classify import classify_snippet, score_sentiment, extract_stance, extract_actions
from desk.ingest import ingest_text, analyze_day, classified_to_dict
from desk.parser import parse_notes, RawSnippet
from desk.strategy import rule_conclude


class ParserTests(unittest.TestCase):
    def test_default_short_content_and_raw(self):
        text = "看好科技\nNVDA\n银行"
        day = parse_notes(text)[0]
        self.assertEqual(day.day,date.today())
        self.assertEqual(day.raw,text)
        self.assertEqual(day.snippets[0].text,text)
        self.assertEqual(day.snippets[0].source,"未署名")

    def test_headers_relative_repeated_dates_and_preamble(self):
        text = "开头观点\n# 2026-05-03\n来源：小王\n利率下降\n2026/5/4\n看好科技\n昨天\n补充观点\n2026年5月3日\n还有观点"
        days = parse_notes(text,date(2026,5,4))
        self.assertEqual([d.day.isoformat() for d in days],["2026-05-03","2026-05-04"])
        self.assertEqual(len(days[0].snippets),3)
        self.assertIn("补充观点",days[0].raw)
        self.assertIn("开头观点",days[1].raw)
        self.assertEqual(days[0].snippets[0].source,"小王")

    def test_negation_overlap(self):
        self.assertGreater(score_sentiment("利空出尽"),0)
        self.assertEqual(score_sentiment("不加仓，不重仓"),0)
        self.assertEqual(extract_stance("不加仓"),"hold")
        self.assertEqual(extract_actions("NVDA 不重仓"),[])


class ModelTransportTests(unittest.TestCase):
    def test_nonweb_request_is_bounded_and_rejects_wrong_json_root(self):
        ask = Mock(return_value={"text":"[]"})
        with patch.dict("sys.modules",{"deepseek_web":SimpleNamespace(ask=ask)}):
            with self.assertRaises(ValueError):
                expand.conclude_with_llm("only classify supplied text")
        kwargs = ask.call_args.kwargs
        self.assertFalse(kwargs["web_search"])
        self.assertFalse(kwargs["require_search"])
        self.assertFalse(kwargs["thinking"])
        self.assertEqual(kwargs["timeout"],20)
        self.assertLessEqual(kwargs["max_tokens"],5000)

    def test_synthesis_uses_flash_with_web_search(self):
        ask = Mock(return_value={
            "text": '{"macro":"流动性还松","industry":"铜是主线","stock":"等回调再看"}',
            "model": "deepseek-flash",
            "urls": [{"title": "公开数据", "url": "https://example.com/macro"}],
        })
        day = {"date": "2026-09-29", "snippets": [{"id": 1, "source": "猫", "text": "下午的平衡被PCB打破了"}]}
        with patch.dict("sys.modules", {"deepseek_web": SimpleNamespace(ask=ask)}):
            result = expand.synthesize_day(day)
        kwargs = ask.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek-flash")
        self.assertTrue(kwargs["web_search"])
        self.assertTrue(kwargs["require_search"])
        self.assertIn("下午的平衡被PCB打破了", ask.call_args.args[0])
        self.assertEqual(result["macro"], "流动性还松")
        self.assertEqual(result["industry"], "铜是主线")
        self.assertEqual(result["stock"], "等回调再看")
        self.assertEqual(result["urls"][0]["url"], "https://example.com/macro")
        self.assertGreaterEqual(kwargs["max_tokens"], 8000)
        self.assertNotIn("只输出一个 JSON", kwargs["system"])
        with self.assertRaises(ValueError):
            expand.synthesize_day({"date": "2026-09-29", "snippets": []})

    def test_synthesis_accepts_chinese_headings(self):
        samples = [
            ('{"宏观":"松","行业":"铜","个股":"等"}', "松", "铜", "等", ""),
            ("## 宏观\n流动性还松\n\n行业：铜是主线\n\n1. 个股：等回调", "流动性还松", "铜是主线", "等回调", ""),
            ("今天先看数据，没有分段。", "今天先看数据，没有分段。", "这一层没有写出判断。", "这一层没有写出判断。", "模型没有按三段标题来写，原文先放在宏观下面。"),
        ]
        day = {"date": "2026-09-29", "snippets": [{"id": 1, "source": "猫", "text": "笔记"}]}
        for text, macro, industry, stock, note in samples:
            ask = Mock(return_value={"text": text, "model": "deepseek-flash", "urls": []})
            with patch.dict("sys.modules", {"deepseek_web": SimpleNamespace(ask=ask)}):
                result = expand.synthesize_day(day)
            self.assertEqual(result["macro"], macro)
            self.assertEqual(result["industry"], industry)
            self.assertEqual(result["stock"], stock)
            self.assertEqual(result["note"], note)
        ask = Mock(return_value={"text": "", "model": "deepseek-flash", "stop_reason": "max_tokens", "urls": []})
        with patch.dict("sys.modules", {"deepseek_web": SimpleNamespace(ask=ask)}):
            with self.assertRaises(ValueError) as caught:
                expand.synthesize_day(day)
        self.assertIn("没有写出判断", str(caught.exception))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = patch.multiple(db,DATA=self.root,DB_PATH=self.root/"test.db")
        self.paths.start()
        self.network = patch.object(expand,"conclude_with_llm",side_effect=AssertionError("unexpected network"))
        self.ask = self.network.start()
        self.day = "2026-05-04"
        self.fallback = date.fromisoformat(self.day)

    def tearDown(self):
        self.network.stop()
        self.paths.stop()
        self.tmp.cleanup()

    def ingest(self,text):
        return ingest_text(text,self.fallback,use_llm=False)

    def test_append_duplicate_preserves_ids_edits_and_expansion(self):
        self.ingest("看好科技")
        original = db.get_day(self.day)
        sid = original["snippets"][0]["id"]
        expanded = db.add_expansion(sid,"paste","","实际公告待核验")
        eid = expanded["snippets"][0]["expansions"][0]["id"]
        db.update_snippet(sid,{"layer":"stock","text":"手动编辑","mood":"偏空"})
        self.ingest("看好科技")
        self.ask.assert_not_called()
        current = db.get_day(self.day)
        self.assertEqual(len(current["snippets"]),1)
        self.assertEqual(current["snippets"][0]["text"],"手动编辑")
        self.ingest("利率下降")
        current = db.get_day(self.day)
        self.assertEqual(len(current["snippets"]),2)
        self.assertEqual(current["snippets"][0]["id"],sid)
        self.assertEqual(current["snippets"][0]["expansions"][0]["id"],eid)
        self.assertIn("看好科技",current["raw"])
        self.assertIn("利率下降",current["raw"])
        self.assertIsNotNone(current["conclusion"])
        self.assertNotIn("已吸收",current["conclusion"]["note"])

    def test_bad_date_atomic(self):
        with self.assertRaises(ValueError):
            self.ingest("2026-05-03\n看好科技\n2026/2/30\n利率")
        self.assertEqual(db.list_days(),[])

    def test_mutations_invalidate_and_mood_consistent(self):
        self.ingest("利率下降")
        sid = db.get_day(self.day)["snippets"][0]["id"]
        changed = db.update_snippet(sid,{"mood":"偏空"})
        self.assertIsNone(changed["conclusion"])
        self.assertEqual(changed["snippets"][0]["sentiment"],-.5)
        analyze_day(self.day,use_llm=False)
        self.assertIsNone(db.add_expansion(sid,"paste","","证据")["conclusion"])

    def test_model_failure_keeps_content_and_note(self):
        self.ask.side_effect = TimeoutError("timeout")
        ingest_text("看好科技",self.fallback)
        current = db.get_day(self.day)
        self.assertEqual(len(current["snippets"]),1)
        self.assertEqual(current["conclusion"]["engine"],"rule")
        self.assertIn("失败",current["conclusion"]["fallback_note"])
        self.assertEqual(self.ask.call_count,1)

    def test_duplicate_no_model_or_conclusion_overwrite(self):
        self.ingest("看好科技")
        before = db.get_day(self.day)
        ingest_text("看好科技",self.fallback,use_llm=True)
        self.ask.assert_not_called()
        self.assertEqual(db.get_day(self.day),before)

    def test_model_success_and_invalid_evidence_fallback(self):
        classification = {"items":[{"id":0,"layer":"stock","layers":["stock"],"industries":[],"tickers":["NVDA"],
            "sentiment":.3,"stance":"watch","viewpoint":"等待业绩验证","confidence":"medium","conditional_plans":[]}]}
        def respond(prompt):
            if "items" in prompt:
                return classification
            packed = db.get_day(self.day)
            result = rule_conclude(packed)
            result["watch"] = ["NVDA"]
            return result
        self.ask.side_effect = respond
        ingest_text("NVDA 看好但等待业绩验证",self.fallback)
        current = db.get_day(self.day)
        self.assertEqual(self.ask.call_count,2)
        self.assertEqual(current["snippets"][0]["extras"]["classification_engine"],"llm")
        self.assertEqual(current["conclusion"]["engine"],"llm")
        invalid = rule_conclude(current)
        invalid["source_ids"] = [9999]
        self.ask.side_effect = None
        self.ask.return_value = invalid
        fallback = analyze_day(self.day)
        self.assertEqual(fallback["conclusion"]["engine"],"rule")
        self.assertIn("fallback_note",fallback["conclusion"])

    def test_concurrent_edit_wins_over_model(self):
        self.ingest("NVDA 观察")
        original = db.get_day(self.day)
        sid = original["snippets"][0]["id"]
        def mutate(prompt):
            db.update_snippet(sid,{"mood":"偏空"})
            return rule_conclude(original)
        self.ask.side_effect = mutate
        current = analyze_day(self.day)
        self.assertIsNone(current["conclusion"])
        self.assertEqual(current["snippets"][0]["mood"],"偏空")

    def test_classification_schema_atomic(self):
        c = classified_to_dict(classify_snippet(RawSnippet(self.fallback,"未署名","NVDA")))
        before = copy.deepcopy(c)
        for bad in (float("nan"),True,2,".3"):
            self.ask.side_effect = None
            self.ask.return_value = {"items":[{"id":0,"layer":"stock","layers":["stock"],"industries":[],"tickers":[],
                "sentiment":bad,"stance":"hold","confidence":"low","viewpoint":"测试","conditional_plans":[]}]}
            with self.assertRaises(ValueError):
                classify_batch([c])
            self.assertEqual(c,before)

    def test_conflicts_and_author_actions_have_evidence(self):
        self.ingest("来源：甲\nNVDA 看好重仓\n来源：乙\nNVDA 看空减仓")
        c = db.get_day(self.day)["conclusion"]
        self.assertTrue(c["conflicts"])
        self.assertTrue(c["author_actions"])
        self.assertTrue(all(a["kind"]=="author_action" and a["evidence_ids"] for a in c["author_actions"]))
        self.assertTrue(all(p["trigger"] and p["invalidation"] and p["evidence_ids"] for p in c["conditional_plans"]))
        self.assertNotIn("做 ",c["headline"])

    def test_fabricated_plan_entity_and_threshold_rejected(self):
        self.ingest("NVDA 观察")
        packed = db.get_day(self.day)
        valid = rule_conclude(packed)
        for field, value in (("subject","虚构股份"),("trigger","价格达到999元")):
            bad = copy.deepcopy(valid)
            bad["conditional_plans"][0][field] = value
            with self.assertRaises(ValueError):
                validate_conclusion(bad,packed)

    def test_unknown_rule_classification_marks_uncertainty(self):
        c = classify_snippet(RawSnippet(self.fallback,"付鹏","随手想法"))
        self.assertEqual(c.extras["confidence"],"low")
        self.assertEqual(c.weight,1)

    def test_classification_count_bounded(self):
        classified = [classified_to_dict(classify_snippet(RawSnippet(self.fallback,"未署名",f"观点{i}"))) for i in range(30)]
        self.ask.side_effect = None
        self.ask.return_value = {"items":[{"id":i,"layer":"industry","layers":["industry"],"industries":[],"tickers":[],
            "sentiment":0,"stance":"hold","confidence":"low","viewpoint":"无明确判断","conditional_plans":[]} for i in range(24)]}
        classify_batch(classified)
        self.assertEqual(self.ask.call_count,1)
        self.assertEqual(classified[24]["extras"]["classification_engine"],"rule")
        self.assertIn("上限",classified[24]["extras"]["classification_note"])


if __name__ == "__main__":
    unittest.main()
