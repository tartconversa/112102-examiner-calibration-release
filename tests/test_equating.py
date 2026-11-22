import unittest

from app.equate import DRIFT_TOLERANCE, build_model, common_pairs
from app.loader import LoadError, load_dataset
from app.models import Dataset, Mark

ROOT_SOURCES = ["sources"]


class EquatingTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT_SOURCES)
        self.model = build_model(self.dataset)

    def test_pairs_match_cross_regraded_common_marks(self):
        pairs = common_pairs(self.dataset)
        # 数据中 10 名考生有跨站同卷复评
        self.assertEqual(len(pairs), 10)
        # B 复评 A 的作业固定高 4 分，A 复评 B 的作业固定低 4 分
        self.assertTrue(all(p.offset == 4 or p.offset == -4 for p in pairs))

    def test_reference_centre_and_adjustment(self):
        self.assertEqual(self.model.reference_centre, "A")
        # B 中心分数折算到 A 参考站需 -4
        self.assertEqual(self.model.centre_adjustment["B"], -4)
        self.assertEqual(self.model.centre_adjustment["A"], 0)

    def test_no_drift_within_chains_in_synthetic_data(self):
        for chain in self.model.chains:
            self.assertLessEqual(chain.max_offset - chain.min_offset,
                                 DRIFT_TOLERANCE)
            self.assertFalse(chain.drift)

    def test_evidence_tiers(self):
        # A2 是有锚点的原评考官；其支撑区间内的成绩为 anchored
        anchored = self.dataset.mark_by_id("M6-common")
        self.assertEqual(self.model.tier_for_mark(anchored), "anchored")
        # 撤销通知没有分数，层级为 none
        revoke = self.dataset.mark_by_id("R15")
        self.assertEqual(self.model.tier_for_mark(revoke), "none")

    def test_unanchored_examiner_is_flagged(self):
        from app.equate import unanchored_examiners
        # A1/B1 的本中心评分没有同卷复评链路（只做过跨站复评人）
        self.assertEqual(set(unanchored_examiners(self.dataset, self.model)),
                         {"A1", "B1"})

    def test_drift_detected_when_offsets_spread(self):
        marks = [
            Mark("x1", "X1", "A", "common", "A2", 60, 10, 11, ""),
            Mark("y1", "X1", "B", "common", "B1", 66, 14, 15, ""),
            Mark("x2", "X2", "A", "common", "A2", 60, 10, 11, ""),
            Mark("y2", "X2", "B", "common", "B1", 60, 14, 15, ""),
        ]
        ds = Dataset(marks=marks)
        model = build_model(ds)
        self.assertTrue(model.chains[0].drift)


if __name__ == "__main__":
    unittest.main()
