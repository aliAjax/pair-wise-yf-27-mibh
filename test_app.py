import base64
import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ProvenanceStore


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProvenanceStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_provenance_and_return_review_flow(self):
        source = self.store.add_source("staff", "馆藏购藏档案", "archive", "ACC-1999-7")
        obj = self.store.create_object("staff", "M-1999-7", "青铜器", "礼器", "市博物馆", "1999年入藏，来源待持续核验。")
        event = self.store.add_event("staff", obj["id"], "acquisition", "1999-07-01", "", "本市", "从私人藏家购入", source["id"], "public")
        evidence = self.store.upload_evidence("staff", obj["id"], "purchase.pdf", base64.b64encode(b"purchase record").decode(), "internal", event["id"])
        self.assertEqual(len(evidence["sha256"]), 64)
        updated = self.store.update_object("staff", obj["id"], {"public_summary": "已完成首轮来源整理。"})
        self.assertEqual(updated["version"], 3)
        claim = self.store.create_claim("claimant1", obj["id"], "王氏家族", "返还藏品")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "材料齐全，进入调查。")
        self.store.sign_visa("reviewer1", obj["id"], "确认来源与内部证据齐全。")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "双方开始协商返还安排。")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "签署返还协议。")
        public_view = self.store.get_object("public", obj["id"])
        self.assertNotIn("current_holder", public_view)
        self.assertEqual(len(public_view["events"]), 1)
        self.assertEqual(public_view["claims"][0]["status"], "resolved_return")
        claimant_view = self.store.get_object("claimant1", obj["id"])
        self.assertEqual(len(claimant_view["claims"]), 1)
        self.assertGreaterEqual(len(self.store.object_history("reviewer1", obj["id"])), 6)

    def test_visibility_and_claim_stage_invariants(self):
        obj = self.store.create_object("staff", "M-2001-2", "手稿", "纸质", "资料室", "公开简介。")
        claim = self.store.create_claim("claimant1", obj["id"], "捐赠人后代", "归还手稿")
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "直接结束。")
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.assertNotIn("claimant_id", self.store.get_object("public", obj["id"])["claims"][0])
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("public", obj["id"], "note", "2020-01-01", "", "馆内", "未授权事件", None, "public")
        self.assertEqual(ctx.exception.status, 403)

    def _documented_object(self, inventory_no="M-2026-9"):
        source = self.store.add_source("staff", "购藏档案", "archive", "REF-9")
        obj = self.store.create_object("staff", inventory_no, "陶俑", "陶器", "库房", "公开简介。")
        event = self.store.add_event("staff", obj["id"], "acquisition", "1990-01-01", "", "本市", "自拍卖行购藏", source["id"], "public")
        self.store.upload_evidence("staff", obj["id"], "dossier.pdf", base64.b64encode(b"internal report").decode(), "internal", event["id"])
        return obj, event

    def test_visa_sign_requires_source_and_internal_evidence(self):
        obj = self.store.create_object("staff", "M-2026-2", "陶俑", "陶器", "库房", "简介。")
        with self.assertRaises(BusinessError) as ctx:
            self.store.sign_visa("reviewer1", obj["id"], "")
        self.assertEqual(ctx.exception.code, "visa_requirements_unmet")
        event = self.store.add_event("staff", obj["id"], "acquisition", "1990-01-01", "", "本市", "购藏", None, "public")
        with self.assertRaises(BusinessError) as ctx:
            self.store.sign_visa("reviewer1", obj["id"], "")
        self.assertIn(event["id"], ctx.exception.details["events_missing_source"])
        source = self.store.add_source("staff", "购藏档案", "archive", "REF-2")
        self.store.attach_event_source("staff", obj["id"], event["id"], source["id"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.sign_visa("reviewer1", obj["id"], "")
        self.assertIn(event["id"], ctx.exception.details["events_missing_internal_evidence"])
        self.store.upload_evidence("staff", obj["id"], "dossier.pdf", base64.b64encode(b"report").decode(), "internal", event["id"])
        visa = self.store.sign_visa("reviewer1", obj["id"], "首轮签证。")
        self.assertEqual(visa["object_version"], 3)
        self.assertEqual(len(visa["digest"]), 64)
        self.assertEqual(visa["summary"]["public_events"], 1)
        status = self.store.visa_status("reviewer1", obj["id"])
        self.assertTrue(status["valid"])
        self.assertEqual(status["visa"]["object_version"], 3)

    def test_visa_invalidation_blocks_and_resign_unblocks(self):
        obj, event = self._documented_object()
        claim = self.store.create_claim("claimant1", obj["id"], "原藏家后人", "返还陶俑")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "进入调查阶段。")
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "未签证应被拦截。")
        self.assertEqual(ctx.exception.code, "visa_required")
        self.store.sign_visa("reviewer1", obj["id"], "签证。")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "签证有效，进入协商。")
        self.store.upload_evidence("staff", obj["id"], "extra.pdf", base64.b64encode(b"new file").decode(), "internal", event["id"])
        status = self.store.visa_status("reviewer1", obj["id"])
        self.assertFalse(status["valid"])
        self.assertIn("证据材料已变更", status["reasons"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "签证失效应被拦截。")
        self.assertEqual(ctx.exception.code, "visa_invalid")
        self.assertIn("证据材料已变更", ctx.exception.details["reasons"])
        visa2 = self.store.sign_visa("reviewer1", obj["id"], "复审后重签。")
        self.assertEqual(visa2["status"], "active")
        self.assertEqual(self.store.visa_status("reviewer1", obj["id"])["visa"]["id"], visa2["id"])
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "重签后完成返还。")

    def test_object_and_event_changes_invalidate_visa(self):
        obj, _ = self._documented_object()
        self.store.sign_visa("reviewer1", obj["id"], "签证。")
        self.store.update_object("staff", obj["id"], {"public_summary": "修订后的公开简介。"})
        self.assertIn("藏品登记信息已变更", self.store.visa_status("reviewer1", obj["id"])["reasons"])
        self.store.sign_visa("reviewer1", obj["id"], "复审重签。")
        source = self.store.add_source("staff", "借展档案", "archive", "REF-3")
        self.store.add_event("staff", obj["id"], "exhibition", "1995-06-01", "", "省城", "借展", source["id"], "public")
        self.assertIn("流转事件或来源记录已变更", self.store.visa_status("staff", obj["id"])["reasons"])

    def test_visa_visibility_by_role(self):
        obj, _ = self._documented_object()
        self.store.sign_visa("reviewer1", obj["id"], "签证。")
        claimant_view = self.store.visa_status("claimant1", obj["id"])
        self.assertTrue(claimant_view["valid"])
        self.assertNotIn("visa", claimant_view)
        with self.assertRaises(BusinessError) as ctx:
            self.store.visa_status("public", obj["id"])
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.sign_visa("staff", obj["id"], "越权签署。")
        self.assertEqual(ctx.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
