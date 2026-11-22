"""排班与带教释放引擎。

约束（任何一条不满足都不得派工，缺口显式上报）：

1. 班次资格：独立班需独立资格（K1 类），监督班需监督实习资格（K3 类），
   监督人需监督权利（K2 类）；规则按任务与模式匹配。
2. 跨站（home != duty.centre）使用成绩时，按处理口径采信：
   strict 要求同卷复评直接覆盖（anchored）；独立班还要求考生本人有
   同卷复评记录。无共同评阅证据者只能补评或有监督实习，不得独立值班。
3. 时间窗：班次必须落在本人可用区间内。
4. 同一人（任何角色）不得同时承担两处时间重叠的承诺，包括同一监督人
   跨站的两条监督工时。
5. 跨站释放不得使来源站在 release_terms 任一窗口的在岗独立能力人数
   低于 min_independent。
6. 已 accepted 的签收是正式承诺，默认锁定；reserved 是暂定，可改派。
   复核使资格失效时，两类班次受影响方式不同，引擎分别给出处置建议。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .equate import EquatingModel
from .models import (
    COMMIT_STATES,
    Dataset,
    DutyRequest,
    Receipt,
    Rule,
    SUPERVISOR_RULE_MODE,
)
from .qualifications import QualificationVerdict, evaluate_candidate

RULE_TASK_CANDIDATE = {"independent": "service", "supervised": "service"}
SUPERVISOR_TASK = "oversight"


@dataclass
class AssignmentCheck:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    basis: QualificationVerdict | None = None
    supervisor_basis: QualificationVerdict | None = None


@dataclass
class Assignment:
    duty: str
    centre: str
    day: int
    start: int
    end: int
    mode: str
    candidate: str
    supervisor: str = ""
    source: str = "planned"          # planned | reserved | accepted | reassigned
    check: AssignmentCheck | None = None


@dataclass
class DutyBlocker:
    duty: str
    kind: str                        # no_candidate / no_supervisor / term_breach / ...
    detail: str


@dataclass
class Plan:
    treatment: str
    as_of_day: int
    assignments: list[Assignment]
    unfillable: list[DutyBlocker]
    term_breaches: list[DutyBlocker]
    locked_commitments: list[Assignment]
    receipt_findings: list[DutyBlocker]


class Scheduler:
    def __init__(self, dataset: Dataset, model: EquatingModel,
                 treatment: str, as_of_day: int):
        self.ds = dataset
        self.model = model
        self.treatment = treatment
        self.as_of_day = as_of_day
        self._verdicts: dict[tuple[str, str], QualificationVerdict] = {}
        self._availability = {a.candidate: a for a in dataset.availability}
        self._home = {a.candidate: a.home for a in dataset.availability}

    # ---------- 基础查询 ----------

    def rule_for(self, task: str, mode: str) -> Rule | None:
        matches = [r for r in self.ds.rules if r.task == task and r.mode == mode]
        return sorted(matches, key=lambda r: r.rule)[-1] if matches else None

    def verdict(self, candidate: str, task: str, mode: str) -> QualificationVerdict | None:
        rule = self.rule_for(task, mode)
        if rule is None:
            return None
        key = (candidate, rule.rule)
        if key not in self._verdicts:
            self._verdicts[key] = evaluate_candidate(
                self.ds, self.model, candidate, rule,
                self.treatment, self.as_of_day)
        return self._verdicts[key]

    def _window_ok(self, candidate: str, duty: DutyRequest) -> bool:
        avail = self._availability.get(candidate)
        if avail is None or avail.available_day != duty.day:
            return False
        return avail.accepted_start <= duty.start and duty.end <= avail.accepted_end

    def _cross_site(self, candidate: str, duty: DutyRequest) -> bool:
        return self._home.get(candidate) != duty.centre

    def _portable(self, candidate: str, task: str, mode: str) -> tuple[bool, str]:
        """跨站采信检查，返回 (是否可采信, 原因)。"""
        verdict = self.verdict(candidate, task, mode)
        if verdict is None or verdict.basis is None:
            return False, "无适用资格规则"
        if not verdict.passes:
            return False, verdict.basis.reason or "资格不达标"
        if self.treatment == "local":
            return False, "本地口径成绩不可跨站直接替换，需共同评阅证据"
        if verdict.basis.tier == "none":
            return False, "没有共同评阅证据：只能安排补评或有监督实习，不得独立值班"
        if verdict.basis.tier == "extrapolated" and self.treatment == "strict":
            return False, "分数超出同卷复评支撑区间，严格口径不采信"
        return True, ""

    # ---------- 人员资格检查 ----------

    def check_candidate(self, candidate: str, duty: DutyRequest) -> AssignmentCheck:
        reasons: list[str] = []
        task = RULE_TASK_CANDIDATE[duty.mode]
        verdict = self.verdict(candidate, task,
                               "independent" if duty.mode == "independent"
                               else "supervised")
        if verdict is None:
            return AssignmentCheck(False, ["缺少对应资格规则"])
        if not verdict.passes:
            reasons.append(f"资格不达标：{verdict.basis.reason if verdict.basis else '无有效成绩'}")
        if not self._window_ok(candidate, duty):
            reasons.append("可用时间窗不覆盖班次")
        if not reasons and self._cross_site(candidate, duty):
            ok, why = self._portable(candidate, task,
                                     "independent" if duty.mode == "independent"
                                     else "supervised")
            if not ok:
                reasons.append(why)
            if duty.mode == "independent" and candidate not in self.model.paired_candidates:
                reasons.append("考生本人无同卷复评记录，不能获得跨站独立值班资格")
        return AssignmentCheck(not reasons, reasons, basis=verdict)

    def check_supervisor(self, supervisor: str, duty: DutyRequest,
                         occupied: list[Assignment]) -> AssignmentCheck:
        reasons: list[str] = []
        verdict = self.verdict(supervisor, SUPERVISOR_TASK, SUPERVISOR_RULE_MODE)
        if verdict is None or not verdict.passes:
            reason = verdict.basis.reason if verdict and verdict.basis else "无监督权利记录"
            reasons.append(f"监督权利不达标：{reason}")
        if supervisor and not self._window_ok(supervisor, duty):
            reasons.append("监督人可用时间窗不覆盖班次")
        for other in occupied:
            people = {other.candidate}
            if other.supervisor:
                people.add(other.supervisor)
            if supervisor in people and other.day == duty.day and not (
                    other.end <= duty.start or duty.end <= other.start):
                reasons.append(
                    f"与已承诺班次 {other.duty}（第 {other.day} 日 "
                    f"{other.start}-{other.end}）时间重叠，同一人不能同时承担两处")
        return AssignmentCheck(not reasons, reasons, supervisor_basis=verdict)

    # ---------- 留守能力 ----------

    def _term_violations(self, assignments: list[Assignment]) -> list[DutyBlocker]:
        breaches: list[DutyBlocker] = []
        for term in self.ds.release_terms:
            home_people = [p for p, h in self._home.items() if h == term.centre]
            # 窗口内留在来源站（未被跨站占用）的独立能力人数
            on_site: list[str] = []
            for person in home_people:
                away = any(
                    a.candidate == person and a.centre != term.centre
                    and a.day == term.day
                    and not (a.end <= term.start or term.end <= a.start)
                    for a in assignments
                )
                if away:
                    continue
                verdict = self.verdict(person, "service", "independent")
                if verdict and verdict.passes:
                    avail = self._availability.get(person)
                    if avail and avail.available_day == term.day and not (
                            avail.accepted_end <= term.start
                            or term.end <= avail.accepted_start):
                        on_site.append(person)
            if len(on_site) < term.min_independent:
                breaches.append(DutyBlocker(
                    duty=f"release@{term.centre}",
                    kind="term_breach",
                    detail=(f"{term.centre} 站第 {term.day} 日 {term.start}-{term.end} "
                            f"窗口仅 {len(on_site)} 名在岗独立能力人员 "
                            f"({sorted(on_site)})，低于最低留守 {term.min_independent}；"
                            f"需补评扩员或减少跨站释放"),
                ))
        return breaches

    # ---------- 既有签收核对 ----------

    def review_receipts(self) -> tuple[list[Assignment], list[DutyBlocker]]:
        locked: list[Assignment] = []
        findings: list[DutyBlocker] = []
        # 同一 duty 取 day 最大的事件；同日按 state 与 event 次序确定
        latest: dict[str, Receipt] = {}
        for receipt in sorted(self.ds.receipts, key=lambda r: (r.day, r.event)):
            # 晚到撤销与签收交错：只重核截至 as_of_day 已经到达的确认
            if receipt.day > self.as_of_day:
                continue
            latest[receipt.duty] = receipt

        for duty_id, receipt in latest.items():
            duty = self.ds.duty_by_id(duty_id)
            if duty is None:
                continue
            candidate_check = self.check_candidate(receipt.candidate, duty)
            supervisor_check = None
            if duty.mode == "supervised":
                if not receipt.supervisor:
                    supervisor_check = AssignmentCheck(
                        False, ["监督班缺少监督人签收"])
                else:
                    supervisor_check = self.check_supervisor(
                        receipt.supervisor, duty, [
                            a for a in locked if a.duty != duty_id])
            assignment = Assignment(
                duty=duty.duty, centre=duty.centre, day=duty.day,
                start=duty.start, end=duty.end, mode=duty.mode,
                candidate=receipt.candidate,
                supervisor=receipt.supervisor or "",
                source=receipt.state if receipt.state in COMMIT_STATES
                else f"receipt:{receipt.state}",
                check=candidate_check,
            )
            problems = list(candidate_check.reasons)
            if supervisor_check:
                problems.extend(supervisor_check.reasons)
            if problems:
                if receipt.state == "accepted":
                    # 正式承诺失效：解除锁定进入重新规划，但失效事实必须留痕
                    severity = "正式承诺已失效，解除锁定并需及时改派"
                else:
                    severity = "暂定承诺受阻，不得视为已确认"
                findings.append(DutyBlocker(
                    duty=duty_id, kind="receipt_invalid",
                    detail=(f"签收 {receipt.event}（{receipt.state}，第 {receipt.day} 日）："
                            f"{severity}：{'；'.join(problems)}"),
                ))
                continue
            if receipt.state in COMMIT_STATES:
                locked.append(assignment)
        return locked, findings

    # ---------- 规划 ----------

    def plan(self) -> Plan:
        locked, findings = self.review_receipts()
        occupied = list(locked)
        assignments: list[Assignment] = []
        unfillable: list[DutyBlocker] = []

        locked_duties = {a.duty for a in locked}
        for duty in sorted(self.ds.duty_requests, key=lambda d: d.duty):
            if duty.duty in locked_duties:
                continue
            candidate_checks = [
                (person, self.check_candidate(person, duty))
                for person in sorted(self._availability)
            ]
            viable = [(p, c) for p, c in candidate_checks if c.ok
                      and not self._person_overlaps(p, duty, occupied)]
            if duty.mode == "supervised":
                # 具备监督权利的人尽量留给监督岗位，避免候选人/监督人同人争用
                viable.sort(key=lambda item: (
                    self.check_supervisor(item[0], duty, occupied).ok,
                    item[0]))
            if not viable:
                tried = "; ".join(
                    f"{p}[{'；'.join(c.reasons)}]" for p, c in candidate_checks)
                unfillable.append(DutyBlocker(
                    duty=duty.duty, kind="no_candidate",
                    detail=f"无合格候选人。排查：{tried}"))
                continue

            chosen: Assignment | None = None
            for person, check in viable:
                supervisor = ""
                sup_check = None
                if duty.mode == "supervised":
                    # 候选人本人不能同时担任该班监督人；尝试所有合格监督人，
                    # 避免“唯一监督人被先选为候选人”的死锁
                    sup = self._find_supervisor(duty, occupied, person)
                    if sup is None:
                        continue
                    supervisor, sup_check = sup
                chosen = Assignment(
                    duty=duty.duty, centre=duty.centre, day=duty.day,
                    start=duty.start, end=duty.end, mode=duty.mode,
                    candidate=person, supervisor=supervisor,
                    source="planned", check=check)
                chosen.check = AssignmentCheck(
                    True, [], basis=check.basis,
                    supervisor_basis=sup_check.supervisor_basis if sup_check else None)
                break
            if chosen is None:
                diagnostics = []
                for person in sorted(self._availability):
                    sup = self.check_supervisor(person, duty, occupied)
                    if not sup.ok:
                        diagnostics.append(f"{person}[{'；'.join(sup.reasons)}]")
                unfillable.append(DutyBlocker(
                    duty=duty.duty, kind="no_supervisor",
                    detail="候选人可安排，但无满足资格且时间不冲突的监督人；"
                           "需补评 oversight 或调整监督工时。监督人排查："
                           + "；".join(diagnostics)))
                continue
            assignments.append(chosen)
            occupied.append(chosen)

        all_assignments = locked + assignments
        breaches = self._term_violations(all_assignments)
        return Plan(
            treatment=self.treatment, as_of_day=self.as_of_day,
            assignments=all_assignments, unfillable=unfillable,
            term_breaches=breaches, locked_commitments=locked,
            receipt_findings=findings,
        )

    def _person_overlaps(self, person: str, duty: DutyRequest,
                         occupied: list[Assignment]) -> bool:
        for other in occupied:
            people = {other.candidate}
            if other.supervisor:
                people.add(other.supervisor)
            if person in people and other.day == duty.day and not (
                    other.end <= duty.start or duty.end <= other.start):
                return True
        return False

    def _find_supervisor(self, duty: DutyRequest, occupied: list[Assignment],
                         candidate: str) -> tuple[str, AssignmentCheck] | None:
        for person in sorted(self._availability):
            if person == candidate:
                continue
            check = self.check_supervisor(person, duty, occupied)
            if check.ok:
                return person, check
        return None

    # ---------- 手工提交/改派 ----------

    def submit(self, duty: DutyRequest, candidate: str,
               supervisor: str = "", occupied: list[Assignment] | None = None
               ) -> AssignmentCheck:
        occupied = occupied or []
        check = self.check_candidate(candidate, duty)
        if duty.mode == "supervised":
            if not supervisor:
                check.reasons.append("监督班必须指定监督人")
                check.ok = False
            else:
                sup = self.check_supervisor(supervisor, duty, occupied)
                check.reasons.extend(sup.reasons)
                check.supervisor_basis = sup.supervisor_basis
                check.ok = not check.reasons
        return check

    def reassign(self, duty: DutyRequest, new_candidate: str,
                 new_supervisor: str, occupied: list[Assignment]) -> Assignment | None:
        """为失效班次寻找接手人；不得削弱原站点保障（留守约束随后统一复核）。"""
        check = self.check_candidate(new_candidate, duty)
        if not check.ok:
            return None
        sup_basis = None
        supervisor = ""
        if duty.mode == "supervised":
            sup = self.check_supervisor(new_supervisor, duty, occupied)
            if not sup.ok:
                return None
            supervisor, sup_basis = new_supervisor, sup.supervisor_basis
        return Assignment(
            duty=duty.duty, centre=duty.centre, day=duty.day,
            start=duty.start, end=duty.end, mode=duty.mode,
            candidate=new_candidate, supervisor=supervisor,
            source="reassigned",
            check=AssignmentCheck(True, [], basis=check.basis,
                                  supervisor_basis=sup_basis),
        )
