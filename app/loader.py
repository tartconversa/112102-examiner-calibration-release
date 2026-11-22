"""CSV 加载、结构校验与批次追加。

基础资料（sources/）不可变；新机构考核与班次资料以批次目录形式放入
data/batches/，每个批次只包含需要追加的同名 CSV 文件。加载时按
批次名字典序合并，并对合并后的整体做引用与完整性校验。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import (
    Availability,
    Dataset,
    DutyRequest,
    Mark,
    Receipt,
    ReleaseTerm,
    Rule,
    TrainingPeriod,
)

TABLES = (
    "marks",
    "training_periods",
    "qualification_rules",
    "duty_requests",
    "release_terms",
    "availability",
    "duty_receipts",
)

HEADERS = {
    "marks": ["mark", "candidate", "centre", "task", "examiner", "score",
              "assessed_day", "known_day", "revokes"],
    "training_periods": ["candidate", "track", "training_day",
                         "duty_observed_day", "supervised_hours"],
    "qualification_rules": ["rule", "task", "min_score", "validity_days", "mode"],
    "duty_requests": ["duty", "centre", "day", "start", "end", "mode"],
    "release_terms": ["centre", "day", "start", "end", "min_independent"],
    "availability": ["candidate", "home", "accepted_start", "accepted_end",
                     "available_day"],
    "duty_receipts": ["event", "duty", "candidate", "supervisor", "day", "state"],
}

INT_FIELDS = {
    "marks": {"assessed_day", "known_day"},
    "training_periods": {"training_day", "duty_observed_day", "supervised_hours"},
    "qualification_rules": {"min_score", "validity_days"},
    "duty_requests": {"day", "start", "end"},
    "release_terms": {"day", "start", "end", "min_independent"},
    "availability": {"accepted_start", "accepted_end", "available_day"},
    "duty_receipts": {"day"},
}

# 主键：同表内同一主键重复且内容不同即冲突；完全相同的行允许幂等重放。
PRIMARY_KEY = {
    "marks": "mark",
    "training_periods": None,
    "qualification_rules": "rule",
    "duty_requests": "duty",
    "release_terms": None,
    "availability": None,
    "duty_receipts": "event",
}

VALID_DUTY_MODES = {"independent", "supervised"}
VALID_RULE_MODES = {"independent", "supervised", "supervisor"}
VALID_RECEIPT_STATES = {"reserved", "accepted", "rejected", "cancelled",
                        "reassigned"}


class LoadError(ValueError):
    """输入资料不满足结构或引用约束。"""


def _read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADERS[path.stem]:
            raise LoadError(
                f"{path}: 表头应为 {HEADERS[path.stem]}，实际为 {reader.fieldnames}"
            )
        rows = list(reader)
    return reader.fieldnames or [], rows


def _coerce(table: str, rows: list[dict[str, str]], source: Path) -> list[dict]:
    coerced: list[dict] = []
    for line, row in enumerate(rows, start=2):
        if set(row) != set(HEADERS[table]) or any(v is None for v in row.values()):
            raise LoadError(f"{source}:{line} 列数或列名与表头不一致")
        for field in INT_FIELDS[table]:
            raw = row[field]
            if raw == "":
                if not (table == "marks" and field == "score"):
                    raise LoadError(f"{source}:{line} {field} 不能为空")
                continue
            try:
                row[field] = int(raw)
            except ValueError as exc:
                raise LoadError(f"{source}:{line} {field} 不是整数: {raw!r}") from exc
        if table == "marks":
            if row["score"] == "":
                # 撤销通知行（如 R15）没有分数，必须引用被撤销记录
                if not row["revokes"]:
                    raise LoadError(f"{source}:{line} 空分数记录必须在 revokes 引用被撤销成绩")
            else:
                try:
                    row["score"] = int(row["score"])
                except ValueError as exc:
                    raise LoadError(f"{source}:{line} score 不是整数") from exc
        coerced.append(row)
    return coerced


def _build_objects(table: str, rows: list[dict]) -> list:
    if table == "marks":
        return [
            Mark(
                mark=r["mark"], candidate=r["candidate"], centre=r["centre"],
                task=r["task"], examiner=r["examiner"],
                score=None if r["score"] == "" else r["score"],
                assessed_day=r["assessed_day"], known_day=r["known_day"],
                revokes=r["revokes"],
            )
            for r in rows
        ]
    if table == "training_periods":
        return [TrainingPeriod(**r) for r in rows]
    if table == "qualification_rules":
        return [Rule(**r) for r in rows]
    if table == "duty_requests":
        return [DutyRequest(**r) for r in rows]
    if table == "release_terms":
        return [ReleaseTerm(**r) for r in rows]
    if table == "availability":
        return [Availability(**r) for r in rows]
    return [Receipt(**r) for r in rows]


def _collect(directories: list[Path]) -> tuple[dict[str, list[dict[str, str]]],
                                                dict[str, list[list[dict[str, str]]]]]:
    merged: dict[str, list[dict[str, str]]] = {t: [] for t in TABLES}
    by_directory: dict[str, list[list[dict[str, str]]]] = {t: [] for t in TABLES}
    seen_files: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            raise LoadError(f"资料目录不存在: {directory}")
        for table in TABLES:
            path = directory / f"{table}.csv"
            if not path.exists():
                continue
            _, rows = _read_table(path)
            rows = _coerce(table, rows, path)
            seen_files.add(path)
            merged[table].extend(rows)
            by_directory[table].append(rows)
    if not seen_files:
        raise LoadError("没有找到任何可加载的 CSV 资料")
    return merged, by_directory


def _validate(merged: dict[str, list[dict]],
              rows_by_directory: dict[str, list[list[dict]]]) -> None:
    # 1) 主键冲突与重复行
    for table, rows in merged.items():
        key = PRIMARY_KEY[table]
        if key:
            by_key: dict[str, dict] = {}
            for row in rows:
                ident = row[key]
                if ident in by_key and by_key[ident] != row:
                    raise LoadError(f"{table}: {key}={ident} 在不同批次中内容冲突")
                by_key[ident] = row
        else:
            # 无主键表：同一批次目录内禁止重复；跨目录完全相同的行视为幂等重放
            for directory_rows in rows_by_directory[table]:
                full = [tuple(r[k] for k in HEADERS[table]) for r in directory_rows]
                if len(set(full)) != len(full):
                    raise LoadError(f"{table}: 同一批次内存在完全重复的行")

    marks = merged["marks"]
    mark_ids = {m["mark"] for m in marks}
    candidates = {m["candidate"] for m in marks}
    # 2) 撤销引用
    for mark in marks:
        if mark["revokes"]:
            if mark["revokes"] not in mark_ids:
                raise LoadError(f"marks: {mark['mark']} 撤销了不存在的记录 {mark['revokes']}")
            target = next(m for m in marks if m["mark"] == mark["revokes"])
            if target["candidate"] != mark["candidate"] or target["task"] != mark["task"]:
                raise LoadError(
                    f"marks: {mark['mark']} 与被撤销记录 {mark['revokes']} 的考生或任务不一致"
                )
            if target["revokes"]:
                raise LoadError(f"marks: 不能撤销一条本身就是撤销通知的记录 {mark['revokes']}")
            if mark["known_day"] < target["known_day"]:
                raise LoadError(
                    f"marks: 撤销 {mark['mark']} 的资料到达日早于原记录 {mark['revokes']}"
                )
    # 一条原记录最多被撤销一次
    revoked_targets = [m["revokes"] for m in marks if m["revokes"]]
    if len(set(revoked_targets)) != len(revoked_targets):
        raise LoadError("marks: 同一条成绩被撤销了多次")

    # 3) 规则模式合法
    for rule in merged["qualification_rules"]:
        if rule["mode"] not in VALID_RULE_MODES:
            raise LoadError(f"qualification_rules: 未知 mode {rule['mode']}")
    for duty in merged["duty_requests"]:
        if duty["mode"] not in VALID_DUTY_MODES:
            raise LoadError(f"duty_requests: 未知 mode {duty['mode']}")
        if not duty["start"] < duty["end"]:
            raise LoadError(f"duty_requests: {duty['duty']} 时间区间无效")
    for receipt in merged["duty_receipts"]:
        if receipt["state"] not in VALID_RECEIPT_STATES:
            raise LoadError(f"duty_receipts: 未知 state {receipt['state']}")

    # 4) 跨表引用
    duty_ids = {d["duty"] for d in merged["duty_requests"]}
    availability_people = {a["candidate"] for a in merged["availability"]}
    for receipt in merged["duty_receipts"]:
        if receipt["duty"] not in duty_ids:
            raise LoadError(f"duty_receipts: {receipt['event']} 引用未知班次 {receipt['duty']}")
        if receipt["candidate"] not in availability_people:
            raise LoadError(
                f"duty_receipts: {receipt['event']} 候选人 {receipt['candidate']} 无可用区间记录"
            )
        if receipt["supervisor"] and receipt["supervisor"] not in availability_people:
            raise LoadError(
                f"duty_receipts: {receipt['event']} 监督人 {receipt['supervisor']} 无可用区间记录"
            )
    for term in merged["training_periods"]:
        if term["candidate"] not in candidates:
            raise LoadError(
                f"training_periods: 考生 {term['candidate']} 没有任何考评记录"
            )
    for avail in merged["availability"]:
        if not avail["accepted_start"] < avail["accepted_end"]:
            raise LoadError(f"availability: {avail['candidate']} 可用区间无效")

    # 5) 同一考生同一任务同一评阅日只允许一条有效成绩（撤销通知除外）
    seen: set[tuple] = set()
    for mark in marks:
        if mark["revokes"]:
            continue
        key = (mark["candidate"], mark["task"], mark["assessed_day"])
        if key in seen:
            raise LoadError(f"marks: {mark['candidate']} 的任务 {mark['task']} 在 "
                            f"第 {mark['assessed_day']} 日存在重复评阅")
        seen.add(key)


def load_dataset(directories: list[str | Path]) -> Dataset:
    """按目录顺序加载并校验，返回合并后的 Dataset。"""
    paths = [Path(d) for d in directories]
    merged, by_directory = _collect(paths)
    _validate(merged, by_directory)
    dataset = Dataset()
    attr_names = {
        "marks": "marks",
        "training_periods": "training_periods",
        "qualification_rules": "rules",
        "duty_requests": "duty_requests",
        "release_terms": "release_terms",
        "availability": "availability",
        "duty_receipts": "receipts",
    }
    for table in TABLES:
        objects = _build_objects(table, merged[table])
        setattr(dataset, attr_names[table], objects)
    return dataset


def validate_batch(directory: str | Path) -> list[str]:
    """校验一个待追加批次目录，返回该批次包含的表名列表。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise LoadError(f"批次目录不存在: {directory}")
    present = [t for t in TABLES if (directory / f"{t}.csv").exists()]
    if not present:
        raise LoadError(f"批次目录 {directory} 中没有可识别的 CSV 文件")
    for table in present:
        path = directory / f"{table}.csv"
        _, rows = _read_table(path)
        _coerce(table, rows, path)
    return present
