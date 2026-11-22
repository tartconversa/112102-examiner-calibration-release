"""资格判定：有效期、撤销与三种评分处理。

三种处理对应训练负责人需要的“人数范围”：

* local      本地口径：按原站分数与阈值判定，仅在当地可解释；
* calibrated 校准口径：用同卷复评得到的中心偏移折算，锚点支撑区间外
               的成绩标注为外推（extrapolated），仍计入但带证据标记；
* strict     严格口径：只有同卷复评链路直接覆盖（anchored）的成绩才能
               跨站采信，无共同评阅证据的成绩一律不达标。

任何处理下，已撤销成绩与超过有效期的成绩都不能达标；撤销通知按
known_day 生效，晚到撤销通过 as_of_day 重放即可反映。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .equate import EquatingModel
from .models import Dataset, Mark, Rule

TREATMENTS = ("local", "calibrated", "strict")
TIER_RANK = {"anchored": 3, "extrapolated": 2, "none": 1}


@dataclass(frozen=True)
class Basis:
    mark_id: str
    centre: str
    examiner: str
    raw_score: int
    score_used: int
    tier: str
    assessed_day: int
    known_day: int
    expires_day: int
    reason: str = ""  # 未达标/不采信原因


@dataclass(frozen=True)
class QualificationVerdict:
    candidate: str
    rule: str
    task: str
    mode: str
    treatment: str
    passes: bool
    min_score: int
    basis: Basis | None


@dataclass
class RuleHeadcount:
    rule: str
    task: str
    mode: str
    treatment: str
    local_pass: int          # 本地口径达标人数（当地可解释）
    portable_pass: int       # 该处理下可跨站采信的达标人数
    blocked_by_gap: list[str] = field(default_factory=list)   # 证据缺口阻断
    expired_or_revoked: list[str] = field(default_factory=list)
    flips_vs_local: list[str] = field(default_factory=list)


def revoked_mark_ids(dataset: Dataset, as_of_day: int | None = None) -> set[str]:
    """截至 as_of_day 已到达的撤销所引用的原成绩标识。"""
    return {
        m.revokes for m in dataset.marks
        if m.revokes and (as_of_day is None or m.known_day <= as_of_day)
    }


def active_marks_for(dataset: Dataset, candidate: str, task: str,
                     as_of_day: int) -> list[Mark]:
    revoked = revoked_mark_ids(dataset, as_of_day)
    return [
        m for m in dataset.marks
        if m.candidate == candidate and m.task == task
        and m.score is not None and m.mark not in revoked
        and not m.revokes  # 撤销通知行本身不是成绩
        and m.known_day <= as_of_day
    ]


def _latest(marks: list[Mark]) -> Mark | None:
    if not marks:
        return None
    return sorted(marks, key=lambda m: (m.assessed_day, m.known_day, m.mark))[-1]


def evaluate_candidate(dataset: Dataset, model: EquatingModel, candidate: str,
                       rule: Rule, treatment: str, as_of_day: int) -> QualificationVerdict:
    mark = _latest(active_marks_for(dataset, candidate, rule.task, as_of_day))
    if mark is None:
        return QualificationVerdict(
            candidate, rule.rule, rule.task, rule.mode, treatment,
            False, rule.min_score, None,
        )

    assert mark.score is not None
    tier = model.tier_for_mark(mark)
    calibrated = model.calibrated_score(mark)
    assert calibrated is not None
    expires = mark.assessed_day + rule.validity_days
    expired = as_of_day > expires

    if treatment == "local":
        score_used = mark.score
        accepted_tier = True
    elif treatment == "calibrated":
        score_used = calibrated
        accepted_tier = True  # 外推仍计入，但 tier 字段保留证据标记
    else:  # strict
        score_used = calibrated
        accepted_tier = tier == "anchored"

    reasons = []
    if expired:
        reasons.append(f"成绩已于第 {expires} 日到期")
    if not accepted_tier:
        reasons.append({
            "extrapolated": "分数落在同卷复评支撑区间之外，严格口径不采信",
            "none": "没有共同评阅证据，严格口径不跨站采信",
        }[tier])
    if score_used < rule.min_score:
        reasons.append(f"采用分数 {score_used} 低于阈值 {rule.min_score}")

    basis = Basis(
        mark_id=mark.mark, centre=mark.centre, examiner=mark.examiner,
        raw_score=mark.score, score_used=score_used, tier=tier,
        assessed_day=mark.assessed_day, known_day=mark.known_day,
        expires_day=expires,
        reason="；".join(reasons),
    )
    return QualificationVerdict(
        candidate, rule.rule, rule.task, rule.mode, treatment,
        passes=not reasons, min_score=rule.min_score, basis=basis,
    )


def all_verdicts(dataset: Dataset, model: EquatingModel, treatment: str,
                 as_of_day: int) -> list[QualificationVerdict]:
    candidates = sorted({m.candidate for m in dataset.marks})
    verdicts: list[QualificationVerdict] = []
    for rule in dataset.rules:
        for candidate in candidates:
            verdicts.append(evaluate_candidate(
                dataset, model, candidate, rule, treatment, as_of_day))
    return verdicts


def headcounts(dataset: Dataset, model: EquatingModel,
               as_of_day: int) -> list[RuleHeadcount]:
    """对每条规则给出三种处理下的达标人数范围与缺口名单。"""
    candidates = sorted({m.candidate for m in dataset.marks})
    out: list[RuleHeadcount] = []

    for rule in dataset.rules:
        per_candidate: dict[str, dict[str, QualificationVerdict]] = {}
        for candidate in candidates:
            per_candidate[candidate] = {
                treatment: evaluate_candidate(
                    dataset, model, candidate, rule, treatment, as_of_day)
                for treatment in TREATMENTS
            }
        local_pass_set = {c for c, vs in per_candidate.items()
                          if vs["local"].passes}

        for treatment in TREATMENTS:
            hc = RuleHeadcount(
                rule=rule.rule, task=rule.task, mode=rule.mode,
                treatment=treatment,
                local_pass=len(local_pass_set), portable_pass=0,
            )
            for candidate, verdicts in per_candidate.items():
                verdict = verdicts[treatment]
                if verdict.passes:
                    hc.portable_pass += 1
                elif candidate in local_pass_set:
                    # 本地能过、本处理下不能过：按阻断原因分类留痕
                    assert verdict.basis is not None
                    if "到期" in verdict.basis.reason:
                        hc.expired_or_revoked.append(candidate)
                    else:
                        hc.blocked_by_gap.append(candidate)
                if (treatment != "local" and candidate in local_pass_set
                        and not verdict.passes):
                    hc.flips_vs_local.append(candidate)
            hc.blocked_by_gap.sort()
            hc.expired_or_revoked.sort()
            hc.flips_vs_local.sort()
            out.append(hc)
    return out


def passing_candidates(dataset: Dataset, model: EquatingModel, rule: Rule,
                       treatment: str, as_of_day: int) -> set[str]:
    return {
        v.candidate for v in all_verdicts(dataset, model, treatment, as_of_day)
        if v.rule == rule.rule and v.passes
    }
