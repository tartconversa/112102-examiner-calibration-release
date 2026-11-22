"""共同评阅（同卷复评）锚定分析。

只有共同任务（common）上存在同卷复评时，两家机构的分数才具备可比较的
实证基础。本模块：

1. 配对同卷复评记录，按考官交接链路汇总偏移；
2. 检测同一链路内的偏移离散度（考官交接后的分数漂移）；
3. 给出以参考站为基准的中心级校准量，并标注每条链路的分数支撑区间；
4. 为任意一条成绩判定跨站使用时的证据层级
   （anchored / extrapolated / none）。

service 与 oversight 从未被共同评阅，跨站套用 common 上得到的偏移只是
任务不变性假设，相关结论会在报告中显式标注。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Dataset, Mark

COMMON_TASK = "common"
# 同一考官链路内最大可接受偏移极差；超过即判定存在交接漂移。
DRIFT_TOLERANCE = 2


@dataclass(frozen=True)
class CommonPair:
    candidate: str
    centre_home: str
    mark_home: str
    examiner_home: str
    score_home: int
    centre_cross: str
    mark_cross: str
    examiner_cross: str
    score_cross: int

    @property
    def offset(self) -> int:
        """复评站分数 - 原评站分数（同一份作业）。"""
        return self.score_cross - self.score_home


@dataclass(frozen=True)
class ChainStat:
    examiner_home: str
    centre_home: str
    examiner_cross: str
    centre_cross: str
    n: int
    mean_offset: float
    min_offset: int
    max_offset: int
    score_min: int
    score_max: int
    candidates: tuple[str, ...]
    drift: bool


@dataclass(frozen=True)
class EquatingModel:
    reference_centre: str
    pairs: tuple[CommonPair, ...]
    chains: tuple[ChainStat, ...]
    # 中心 -> 折算到参考站分数所需的调整量
    centre_adjustment: dict[str, int]
    # 考官（作为原评人时）-> 链路统计
    examiner_chain: dict[str, ChainStat]
    # 有同卷复评覆盖的考生
    paired_candidates: frozenset[str]

    def tier_for_mark(self, mark: Mark) -> str:
        """跨站使用一条成绩时的证据层级。"""
        chain = self.examiner_chain.get(mark.examiner)
        if chain is None or mark.score is None:
            return "none"
        if chain.score_min <= mark.score <= chain.score_max:
            return "anchored"
        return "extrapolated"

    def calibrated_score(self, mark: Mark) -> int | None:
        if mark.score is None:
            return None
        return mark.score + self.centre_adjustment.get(mark.centre, 0)


def _active_common_marks(dataset: Dataset) -> list[Mark]:
    revoked_ids = {m.revokes for m in dataset.marks if m.revokes}
    return [
        m for m in dataset.marks
        if m.task == COMMON_TASK and not m.revokes and m.mark not in revoked_ids
        and m.score is not None
    ]


def common_pairs(dataset: Dataset) -> list[CommonPair]:
    """按考生配对同卷复评：同一考生的两条 common 记录互为原评/复评。"""
    by_candidate: dict[str, list[Mark]] = {}
    for mark in _active_common_marks(dataset):
        by_candidate.setdefault(mark.candidate, []).append(mark)

    pairs: list[CommonPair] = []
    for candidate in sorted(by_candidate):
        records = sorted(by_candidate[candidate], key=lambda m: m.assessed_day)
        if len(records) < 2:
            continue
        if len(records) > 2:
            # 数据契约已禁止同任务同日重复；多于两条时保留最早的原评与最近的复评
            records = [records[0], records[-1]]
        home, cross = records
        pairs.append(CommonPair(
            candidate=candidate,
            centre_home=home.centre, mark_home=home.mark,
            examiner_home=home.examiner, score_home=home.score,  # type: ignore[arg-type]
            centre_cross=cross.centre, mark_cross=cross.mark,
            examiner_cross=cross.examiner, score_cross=cross.score,  # type: ignore[arg-type]
        ))
    return pairs


def chain_stats(pairs: list[CommonPair]) -> list[ChainStat]:
    grouped: dict[tuple[str, str, str, str], list[CommonPair]] = {}
    for pair in pairs:
        key = (pair.examiner_home, pair.centre_home,
               pair.examiner_cross, pair.centre_cross)
        grouped.setdefault(key, []).append(pair)

    stats: list[ChainStat] = []
    for key, group in sorted(grouped.items()):
        offsets = [p.offset for p in group]
        homes = [p.score_home for p in group]
        examiner_home, centre_home, examiner_cross, centre_cross = key
        stats.append(ChainStat(
            examiner_home=examiner_home, centre_home=centre_home,
            examiner_cross=examiner_cross, centre_cross=centre_cross,
            n=len(group),
            mean_offset=sum(offsets) / len(offsets),
            min_offset=min(offsets), max_offset=max(offsets),
            score_min=min(homes), score_max=max(homes),
            candidates=tuple(sorted(p.candidate for p in group)),
            drift=(max(offsets) - min(offsets)) > DRIFT_TOLERANCE,
        ))
    return stats


def build_model(dataset: Dataset) -> EquatingModel:
    pairs = common_pairs(dataset)
    chains = chain_stats(pairs)
    centres = sorted({m.centre for m in dataset.marks})
    reference = centres[0] if centres else ""

    # 中心级调整量：把该中心分数折算到参考站。按链路样本量加权平均。
    raw: dict[str, list[tuple[int, int]]] = {c: [] for c in centres}
    examiner_chain: dict[str, ChainStat] = {}
    for chain in chains:
        # 复评站在同卷上高出 mean_offset，故原评站 -> 参考站的折算
        # 以“复评站视角”表达：要消除偏移，需减去偏移。
        raw[chain.centre_home].append((round(chain.mean_offset), chain.n))
        examiner_chain[chain.examiner_home] = chain
    adjustment: dict[str, int] = {}
    for centre in centres:
        if centre == reference:
            adjustment[centre] = 0
            continue
        samples = raw.get(centre) or []
        if not samples:
            adjustment[centre] = 0  # 无锚点：不校准，证据层级会拦截跨站使用
            continue
        total_n = sum(n for _, n in samples)
        adjustment[centre] = round(sum(v * n for v, n in samples) / total_n)

    return EquatingModel(
        reference_centre=reference,
        pairs=tuple(pairs),
        chains=tuple(chains),
        centre_adjustment=adjustment,
        examiner_chain=examiner_chain,
        paired_candidates=frozenset(p.candidate for p in pairs),
    )


def coverage_gaps(dataset: Dataset, model: EquatingModel) -> dict[str, list[str]]:
    """没有任何同卷复评覆盖的考生，按中心分组。"""
    gaps: dict[str, list[str]] = {}
    for candidate in sorted({m.candidate for m in dataset.marks}):
        if candidate in model.paired_candidates:
            continue
        home = next(m.centre for m in dataset.marks if m.candidate == candidate)
        gaps.setdefault(home, []).append(candidate)
    return gaps


def unanchored_examiners(dataset: Dataset, model: EquatingModel) -> list[str]:
    """承担过本中心评分、但没有任何同卷复评链路支撑的考官。"""
    homes = {m.examiner for m in dataset.marks if not m.revokes}
    return sorted(homes - set(model.examiner_chain))
