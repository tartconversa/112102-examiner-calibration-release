"""业务数据模型。所有日期均为相对整数日，时间为整点小时。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mark:
    mark: str
    candidate: str
    centre: str
    task: str
    examiner: str
    score: int | None
    assessed_day: int
    known_day: int
    revokes: str


@dataclass(frozen=True)
class Rule:
    rule: str
    task: str
    min_score: int
    validity_days: int
    mode: str


@dataclass(frozen=True)
class TrainingPeriod:
    candidate: str
    track: str
    training_day: int
    duty_observed_day: int
    supervised_hours: int


@dataclass(frozen=True)
class DutyRequest:
    duty: str
    centre: str
    day: int
    start: int
    end: int
    mode: str


@dataclass(frozen=True)
class Availability:
    candidate: str
    home: str
    accepted_start: int
    accepted_end: int
    available_day: int


@dataclass(frozen=True)
class ReleaseTerm:
    centre: str
    day: int
    start: int
    end: int
    min_independent: int


@dataclass(frozen=True)
class Receipt:
    event: str
    duty: str
    candidate: str
    supervisor: str
    day: int
    state: str


# 班次模式 -> 候选人/监督人需要满足的规则模式
DUTY_RULE_MODE = {
    "independent": "independent",   # 独立值班：K1 类独立资格
    "supervised": "supervised",     # 监督下值班：K3 类监督实习资格
}
SUPERVISOR_RULE_MODE = "supervisor"  # 带教监督权利：K2 类
COMMIT_STATES = {"reserved", "accepted"}


@dataclass
class Dataset:
    marks: list[Mark] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    training_periods: list[TrainingPeriod] = field(default_factory=list)
    duty_requests: list[DutyRequest] = field(default_factory=list)
    availability: list[Availability] = field(default_factory=list)
    release_terms: list[ReleaseTerm] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)

    def mark_by_id(self, mark_id: str) -> Mark | None:
        for mark in self.marks:
            if mark.mark == mark_id:
                return mark
        return None

    def duty_by_id(self, duty_id: str) -> DutyRequest | None:
        for duty in self.duty_requests:
            if duty.duty == duty_id:
                return duty
        return None
