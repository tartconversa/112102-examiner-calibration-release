import unittest

from app.loader import load_dataset
from app.training import (
    POST_TRAINING,
    PRE_OBSERVATION,
    classify_periods,
    evidence_use,
    summarize,
)

ROOT_SOURCES = ["sources"]


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT_SOURCES)

    def test_observation_before_training_is_excluded_from_outcome(self):
        classified = classify_periods(self.dataset)
        p27 = [c for c in classified if c.period.candidate == "P27"]
        roles = {c.period.duty_observed_day: c.role for c in p27}
        # 观察日 11 < 培训开始 13：只能作观察前依据
        self.assertEqual(roles[11], PRE_OBSERVATION)
        self.assertEqual(roles[20], POST_TRAINING)

    def test_same_experience_never_counts_twice(self):
        for item in classify_periods(self.dataset):
            uses = evidence_use(item)
            self.assertEqual(sum(uses.values()), 1)

    def test_summary_separates_hour_pools(self):
        summary = {s.candidate: s for s in summarize(self.dataset)}
        p27 = summary["P27"]
        self.assertEqual(p27.post_training_hours, 4)
        self.assertEqual(p27.pre_observation_hours, 2)
        # 其他人没有观察前经历
        self.assertEqual(summary["P6"].pre_observation_events, 0)
        self.assertEqual(summary["P6"].post_training_hours, 4)


if __name__ == "__main__":
    unittest.main()
