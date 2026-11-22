"""下一轮共同评阅的覆盖优先级。

排序目标：把有限的同卷复评资源先投给“影响排班且证据最薄”的人。
得分越高越优先，评分因子：

1. 在可用名册上（能直接影响近期排班）；
2. 没有任何同卷复评覆盖（person 级证据缺口）；
3. 本地成绩贴近规则阈值，校准后可能翻转达标结论；
4. 当前有班次因证据缺口无法落位；
5. 承担监督权利相关任务（oversight）且无锚点。
"""

from __future__ import annotations

from dataclasses import dataclass

from .equate import EquatingModel
from .models import Dataset
from .qualifications import active_marks_for


@dataclass(frozen=True)
class PriorityRow:
    candidate: str
    home: str
    score: int
    reasons: tuple[str, ...]
    score_value: int


def review_priority(dataset: Dataset, model: EquatingModel,
                    as_of_day: int, blocked_duties: set[str] | None = None
                    ) -> list[PriorityRow]:
    blocked_duties = blocked_duties or set()
    roster = {a.candidate for a in dataset.availability
             if a.available_day == as_of_day}
    homes = {a.candidate: a.home for a in dataset.availability}
    rows: list[PriorityRow] = []

    for candidate in sorted({m.candidate for m in dataset.marks}):
        reasons: list[str] = []
        value = 0
        if candidate in roster:
            value += 40
            reasons.append("在第 %d 日可用名册上" % as_of_day)
        home = homes.get(candidate) or next(
            m.centre for m in dataset.marks if m.candidate == candidate)

        common = active_marks_for(dataset, candidate, "common", as_of_day)
        latest = sorted(common, key=lambda m: (m.assessed_day, m.mark))[-1] if common else None
        score = latest.score if latest and latest.score is not None else 0

        if candidate not in model.paired_candidates:
            value += 30
            reasons.append("无同卷复评覆盖")
        elif latest and model.tier_for_mark(latest) == "extrapolated":
            value += 15
            reasons.append("现有锚点超出支撑区间（外推）")

        # 阈值贴近度：任一规则 ±6 分内都视为边界人员
        for rule in dataset.rules:
            marks = active_marks_for(dataset, candidate, rule.task, as_of_day)
            if not marks:
                continue
            top = sorted(marks, key=lambda m: (m.assessed_day, m.mark))[-1]
            if top.score is not None and abs(top.score - rule.min_score) <= 6:
                value += 12
                reasons.append(
                    f"{rule.task} 分数 {top.score} 贴近 {rule.rule} 阈值 "
                    f"{rule.min_score}（±6）")
                break

        oversight = active_marks_for(dataset, candidate, "oversight", as_of_day)
        if oversight and candidate not in model.paired_candidates:
            value += 8
            reasons.append("监督权利任务缺少共同评阅支撑")

        if candidate in roster and blocked_duties:
            value += 6
            reasons.append("存在因证据缺口无法落位的班次")

        if reasons:
            rows.append(PriorityRow(candidate, home, score, tuple(reasons), value))

    rows.sort(key=lambda r: (-r.score_value, r.home, r.candidate))
    return rows
