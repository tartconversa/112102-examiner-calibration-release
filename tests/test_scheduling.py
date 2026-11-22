import unittest

from app.equate import build_model
from app.loader import LoadError, load_dataset
from app.scheduling import Scheduler

ROOT_SOURCES = ["sources"]


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT_SOURCES)
        self.model = build_model(self.dataset)

    def test_revocation_invalidates_accepted_supervision_commitment(self):
        # 截至第 27 日：撤销未到达，P15 仍有监督权利；但 P27 分数本就不达标，
        # 因此第 25 日的 reserved 暂定承诺会被标记（暂定与正式承诺处置不同）
        early = Scheduler(self.dataset, self.model, "local", 27).plan()
        early_d2 = next(f for f in early.receipt_findings if f.duty == "D2")
        self.assertIn("reserved", early_d2.detail)
        self.assertNotIn("accepted", early_d2.detail)
        # 截至第 29 日：撤销已生效，accepted 签收失效；独立班 D1 不受影响
        plan = Scheduler(self.dataset, self.model, "local", 29).plan()
        duties = {f.duty: f.detail for f in plan.receipt_findings}
        self.assertIn("D2", duties)
        self.assertNotIn("D1", duties)
        self.assertIn("accepted", duties["D2"])

    def test_supervised_candidate_below_threshold_is_rejected(self):
        scheduler = Scheduler(self.dataset, self.model, "local", 29)
        duty = self.dataset.duty_by_id("D2")  # supervised
        check = scheduler.submit(duty, "P27", "P21")
        self.assertFalse(check.ok)
        self.assertTrue(any("资格不达标" in r for r in check.reasons))

    def test_supervisor_double_booking_is_blocked(self):
        from app.scheduling import Assignment, AssignmentCheck
        scheduler = Scheduler(self.dataset, self.model, "local", 29)
        d2 = self.dataset.duty_by_id("D2")  # B 9-12
        d3 = self.dataset.duty_by_id("D3")  # A 10-12，时间重叠
        # P33 是 B 站本地 K3 候选人，P21 具备 K2 监督权利
        first = scheduler.submit(d2, "P33", "P21")
        self.assertTrue(first.ok)
        prior = Assignment(duty="D2", centre="B", day=29, start=9, end=12,
                           mode="supervised", candidate="P33",
                           supervisor="P21", source="planned",
                           check=AssignmentCheck(True))
        second = scheduler.submit(d3, "P6", "P21", occupied=[prior])
        self.assertFalse(second.ok)
        self.assertTrue(any("重叠" in r for r in second.reasons))

    def test_cross_site_independent_requires_paired_evidence(self):
        scheduler = Scheduler(self.dataset, self.model, "strict", 29)
        d1 = self.dataset.duty_by_id("D1")  # B independent
        # P8 是 A 站人，无同卷复评：跨站独立值班被拒
        check = scheduler.check_candidate("P8", d1)
        self.assertFalse(check.ok)
        # P33 有同卷复评且折算后达标，可在 B 本地独立值班
        self.assertTrue(scheduler.check_candidate("P33", d1).ok)

    def test_release_term_breach_is_reported_not_silently_broken(self):
        plan = Scheduler(self.dataset, self.model, "local", 29).plan()
        breaches = [b for b in plan.term_breaches if "A 站" in b.detail]
        self.assertTrue(breaches)
        # A 站上午窗口最低 2 人，名册中仅 P8 具备独立资格
        self.assertIn("低于最低留守 2", breaches[0].detail)

    def test_reassign_finds_timely_cover_and_keeps_audit_basis(self):
        # 场景设在第 27 日：撤销通知（第 28 日到达）尚未生效，P15 仍是合规监督人
        scheduler = Scheduler(self.dataset, self.model, "local", 27)
        d2 = self.dataset.duty_by_id("D2")
        locked, _ = scheduler.review_receipts()
        occupied = [a for a in locked if a.duty != "D2"]
        assignment = scheduler.reassign(d2, "P33", "P15", occupied)
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.source, "reassigned")
        self.assertIsNotNone(assignment.check.basis)
        # 第 29 日重放同一改派：P15 资格已失效，改派必须被拒绝
        late = Scheduler(self.dataset, self.model, "local", 29)
        self.assertIsNone(late.reassign(d2, "P33", "P15", occupied))

    def test_no_supervisor_available_for_station_a_supervised_duty(self):
        # strict 口径下名册内无 anchored K2：D3 报 no_supervisor 缺口
        plan = Scheduler(self.dataset, self.model, "strict", 29).plan()
        d3 = next((b for b in plan.unfillable if b.duty == "D3"), None)
        self.assertIsNotNone(d3)

    def test_availability_window_must_cover_duty(self):
        scheduler = Scheduler(self.dataset, self.model, "local", 29)
        d4 = self.dataset.duty_by_id("D4")  # A 13-17
        # P15 仅 8-12 可用
        self.assertFalse(scheduler.check_candidate("P15", d4).ok)
        # P8 8-17 可用且 K1 达标
        self.assertTrue(scheduler.check_candidate("P8", d4).ok)


if __name__ == "__main__":
    unittest.main()
