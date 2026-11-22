import unittest

from app.equate import build_model
from app.loader import load_dataset
from app.models import Rule
from app.qualifications import (
    TREATMENTS,
    evaluate_candidate,
    headcounts,
    revoked_mark_ids,
)

ROOT_SOURCES = ["sources"]
K1 = Rule("K1", "service", 68, 30, "independent")
K2 = Rule("K2", "oversight", 73, 26, "supervisor")
K3 = Rule("K3", "service", 58, 20, "supervised")


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT_SOURCES)
        self.model = build_model(self.dataset)

    def test_revoked_mark_never_passes_any_treatment(self):
        self.assertIn("M15-oversight", revoked_mark_ids(self.dataset))
        for treatment in TREATMENTS:
            verdict = evaluate_candidate(
                self.dataset, self.model, "P15", K2, treatment, 29)
            self.assertFalse(verdict.passes, treatment)
            self.assertIsNone(verdict.basis)

    def test_revocation_is_late_and_replayable(self):
        # 第 27 日撤销通知（known_day=28）尚未到达：P15 当时具备监督权利
        before = evaluate_candidate(self.dataset, self.model, "P15", K2,
                                    "local", 27)
        self.assertTrue(before.passes)
        # 第 28 日晚到撤销到达后立即失效
        after = evaluate_candidate(self.dataset, self.model, "P15", K2,
                                   "local", 28)
        self.assertFalse(after.passes)

    def test_calibration_flips_borderline_b_candidate(self):
        # P21 为 B 站考生，service 75：本地过 K1(68)，折算 71 仍过
        local = evaluate_candidate(self.dataset, self.model, "P21", K1,
                                   "local", 29)
        calib = evaluate_candidate(self.dataset, self.model, "P21", K1,
                                   "calibrated", 29)
        self.assertTrue(local.passes and calib.passes)
        self.assertEqual(calib.basis.score_used, 71)
        # P32 service 69：本地过 K1(68)，B 站折算 -4 后 65 翻转
        p32_local = evaluate_candidate(self.dataset, self.model, "P32", K1,
                                       "local", 29)
        p32_calib = evaluate_candidate(self.dataset, self.model, "P32", K1,
                                       "calibrated", 29)
        self.assertTrue(p32_local.passes)
        self.assertFalse(p32_calib.passes)

    def test_strict_rejects_unanchored_home_marks(self):
        # A1 无锚点：A 站高分但只由 A1 评分的考生，严格口径不采信
        strict = evaluate_candidate(self.dataset, self.model, "P8", K1,
                                    "strict", 29)
        self.assertFalse(strict.passes)
        self.assertIn("没有共同评阅证据", strict.basis.reason)
        # 有同卷复评的 P9（A2 原评，B1 复评）折算后仍达标
        ok = evaluate_candidate(self.dataset, self.model, "P9", K1,
                                "strict", 29)
        self.assertTrue(ok.passes)
        self.assertEqual(ok.basis.tier, "anchored")

    def test_headcount_ranges_shrink_with_stricter_treatment(self):
        stats = headcounts(self.dataset, self.model, 29)
        k1 = {h.treatment: h.portable_pass for h in stats if h.rule == "K1"}
        self.assertEqual(k1["local"], 18)
        self.assertEqual(k1["calibrated"], 16)
        self.assertEqual(k1["strict"], 6)
        # 证据缺口阻断名单必须可复核
        strict_hc = next(h for h in stats if h.rule == "K1"
                         and h.treatment == "strict")
        self.assertIn("P8", strict_hc.blocked_by_gap)

    def test_validity_window_expires(self):
        # K3 有效期 20 天；把 as_of 推到评阅后 21 天即到期
        expired = evaluate_candidate(self.dataset, self.model, "P4", K3,
                                     "local", 11 + 21)
        self.assertFalse(expired.passes)
        self.assertIn("到期", expired.basis.reason)

    def test_supervised_threshold_is_separate_rule(self):
        # P27 service 54：达不到 K3 的 58，任何口径都不能做监督实习
        for treatment in TREATMENTS:
            v = evaluate_candidate(self.dataset, self.model, "P27", K3,
                                   treatment, 29)
            self.assertFalse(v.passes, treatment)


if __name__ == "__main__":
    unittest.main()
