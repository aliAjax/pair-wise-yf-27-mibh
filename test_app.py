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
        attestation = self.store.attest_object("reviewer1", obj["id"], "首轮来源签证")
        self.assertTrue(attestation["valid"])
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

    def test_attestation_lifecycle_and_re_attestation(self):
        source = self.store.add_source("staff", "战时流转档案", "archive", "WAR-1944-3")
        obj = self.store.create_object("staff", "M-1944-3", "油画", "绘画", "库房", "战时流转记录待核。")
        event = self.store.add_event("staff", obj["id"], "transfer", "1944-05-01", "", "旧港", "战时易手", source["id"], "public")
        self.store.upload_evidence("staff", obj["id"], "ledger.pdf", base64.b64encode(b"ledger").decode(), "internal", event["id"])
        claim = self.store.create_claim("claimant1", obj["id"], "原藏家后裔", "返还油画")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "开始来源调查。")
        # 未签证不能进入协商
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "尝试直接协商。")
        self.assertEqual(ctx.exception.code, "attestation_required")
        first = self.store.attest_object("reviewer1", obj["id"], "首轮签证")
        self.assertTrue(first["valid"])
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "签证有效，进入协商。")
        # 补了一条公开流转事件，签证失效，完成返还被挡住
        event2 = self.store.add_event("staff", obj["id"], "exhibition", "1972-03-01", "", "首都", "战后公开展出", None, "public")
        status = self.store.attestation_status("reviewer1", obj["id"])
        self.assertFalse(status["valid"])
        self.assertIn("新增 1 条流转事件", status["invalid_reason"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "尝试直接返还。")
        self.assertEqual(ctx.exception.code, "attestation_required")
        # 补齐材料：补登记来源 + 内部证据，审查员重签后完成返还
        self.store.amend_event_source("staff", event2["id"], source["id"])
        self.store.upload_evidence("staff", obj["id"], "exhibition.pdf", base64.b64encode(b"catalog").decode(), "internal", event2["id"])
        second = self.store.attest_object("reviewer1", obj["id"], "复审重签")
        self.assertTrue(second["valid"])
        self.assertGreater(second["object_version"], first["object_version"])
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "复审通过，完成返还。")
        view = self.store.get_object("reviewer1", obj["id"])
        self.assertEqual(view["claims"][0]["status"], "resolved_return")
        self.assertTrue(view["attestation"]["valid"])

    def test_attestation_incomplete_and_material_changes(self):
        obj = self.store.create_object("staff", "M-2010-9", "陶俑", "陶塑", "展厅", "简介。")
        event = self.store.add_event("staff", obj["id"], "acquisition", "2010-01-01", "", "本市", "征集入藏", None, "public")
        # 公开事件缺来源和内部证据，不能签证
        with self.assertRaises(BusinessError) as ctx:
            self.store.attest_object("reviewer1", obj["id"], "")
        self.assertEqual(ctx.exception.code, "attestation_incomplete")
        self.assertEqual(len(ctx.exception.detail["missing"]), 2)
        source = self.store.add_source("staff", "征集档案", "archive", "ACQ-2010-9")
        self.store.amend_event_source("staff", event["id"], source["id"])
        self.store.upload_evidence("staff", obj["id"], "acq.pdf", base64.b64encode(b"acq").decode(), "internal", event["id"])
        self.store.attest_object("reviewer1", obj["id"], "补齐后签证")
        # 藏品登记信息变化 → 签证失效
        self.store.update_object("staff", obj["id"], {"public_summary": "更新后的简介。"})
        status = self.store.attestation_status("staff", obj["id"])
        self.assertEqual(status["status"], "invalid")
        self.assertIn("藏品登记信息已变更", status["invalid_reason"])
        # 证据变化 → 签证失效
        self.store.attest_object("reviewer1", obj["id"], "重签")
        self.store.upload_evidence("staff", obj["id"], "extra.pdf", base64.b64encode(b"extra").decode(), "internal", event["id"])
        status = self.store.attestation_status("reviewer1", obj["id"])
        self.assertFalse(status["valid"])
        self.assertIn("证据", status["invalid_reason"])
        # 非审查员不能签证；公众看不到内部摘要
        with self.assertRaises(BusinessError) as ctx:
            self.store.attest_object("staff", obj["id"], "")
        self.assertEqual(ctx.exception.status, 403)
        public_view = self.store.attestation_status("public", obj["id"])
        self.assertNotIn("attestation", public_view)
        self.assertIn("invalid_reason", public_view)


if __name__ == "__main__":
    unittest.main()
