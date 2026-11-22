"""带教/训练经历的角色分离。

同一段上班经历只能扮演一个角色：

* 观察日 >= 培训开始日：属于本期训练成效（post_training），可用于评估
  训练项目是否带来能力；
* 观察日 < 培训开始日：发生在培训之前，只能作为观察前能力依据
  （pre_observation），不能进入训练成效，也不能反过来证明训练效果。

训练成效与观察前依据分开汇总，杜绝同一次带教经验被双重使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Dataset, TrainingPeriod

POST_TRAINING = "post_training"          # 训练成效证据
PRE_OBSERVATION = "pre_observation"       # 观察前能力依据


@dataclass(frozen=True)
class ClassifiedPeriod:
    period: TrainingPeriod
    role: str
    valid: bool
    note: str


@dataclass(frozen=True)
class CandidateTrainingSummary:
    candidate: str
    tracks: tuple[str, ...]
    post_training_hours: int
    post_training_events: int
    pre_observation_hours: int
    pre_observation_events: int


def classify_periods(dataset: Dataset) -> list[ClassifiedPeriod]:
    out: list[ClassifiedPeriod] = []
    for period in dataset.training_periods:
        if period.duty_observed_day >= period.training_day:
            out.append(ClassifiedPeriod(
                period, POST_TRAINING, True,
                f"观察日 {period.duty_observed_day} 不早于培训开始日 "
                f"{period.training_day}，计入训练成效",
            ))
        else:
            out.append(ClassifiedPeriod(
                period, PRE_OBSERVATION, False,
                f"观察日 {period.duty_observed_day} 早于培训开始日 "
                f"{period.training_day}，只能作观察前能力依据，不计入训练成效",
            ))
    return out


def summarize(dataset: Dataset) -> list[CandidateTrainingSummary]:
    classified = classify_periods(dataset)
    by_candidate: dict[str, list[ClassifiedPeriod]] = {}
    for item in classified:
        by_candidate.setdefault(item.period.candidate, []).append(item)

    summaries: list[CandidateTrainingSummary] = []
    for candidate in sorted(by_candidate):
        items = by_candidate[candidate]
        post = [i for i in items if i.role == POST_TRAINING]
        pre = [i for i in items if i.role == PRE_OBSERVATION]
        summaries.append(CandidateTrainingSummary(
            candidate=candidate,
            tracks=tuple(sorted({i.period.track for i in items})),
            post_training_hours=sum(i.period.supervised_hours for i in post),
            post_training_events=len(post),
            pre_observation_hours=sum(i.period.supervised_hours for i in pre),
            pre_observation_events=len(pre),
        ))
    return summaries


def evidence_use(item: ClassifiedPeriod) -> dict[str, bool]:
    """同一段经历允许进入的证据用途（互斥）。"""
    return {
        "training_outcome": item.role == POST_TRAINING,
        "pre_observation_basis": item.role == PRE_OBSERVATION,
    }
