"""命令行入口。

训练负责人：
  python -m app.cli equating|headcount|candidate|training|priority|ingest
排班人员：
  python -m app.cli plan|verify|submit|confirm|reassign|audit-trail
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .audit import Journal
from .equate import build_model, coverage_gaps, unanchored_examiners
from .loader import LoadError, load_dataset, validate_batch
from .models import DutyRequest
from .priority import review_priority
from .qualifications import TREATMENTS, all_verdicts, headcounts
from .scheduling import Scheduler
from .training import classify_periods, summarize

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def data_dirs(root: Path) -> list[Path]:
    dirs = [root / "sources"]
    batch_root = root / "data" / "batches"
    if batch_root.exists():
        dirs.extend(sorted(p for p in batch_root.iterdir() if p.is_dir()))
    return dirs


def load(root: Path):
    dataset = load_dataset(data_dirs(root))
    model = build_model(dataset)
    return dataset, model


# ---------------- 输出辅助 ----------------

def _table(headers: list[str], rows: list[list[str]]) -> str:
    width = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            width[i] = max(width[i], len(str(cell)))
    line = lambda r: "  ".join(str(c).ljust(width[i]) for i, c in enumerate(r))
    out = [line(headers), line(["-" * w for w in width])]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def _emit(payload: dict, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)


# ---------------- 训练负责人命令 ----------------

def cmd_equating(args) -> None:
    dataset, model = load(args.root)
    rows = [[
        c.examiner_home, c.centre_home, c.examiner_cross, c.centre_cross,
        str(c.n), f"{c.mean_offset:+.1f}", f"{c.min_offset:+d}/{c.max_offset:+d}",
        f"{c.score_min}-{c.score_max}",
        "是" if c.drift else "否", ",".join(c.candidates),
    ] for c in model.chains]
    text = [
        f"参考站：{model.reference_centre}（中心调整量："
        f"{', '.join(f'{k}{v:+d}' for k, v in sorted(model.centre_adjustment.items()))}）",
        f"同卷复评对数：{len(model.pairs)}；覆盖考生："
        f"{len(model.paired_candidates)} 人",
        _table(["原评考官", "原评站", "复评考官", "复评站", "对数", "平均偏移",
                "偏移区间", "分数支撑", "交接漂移", "覆盖考生"], rows),
    ]
    gaps = coverage_gaps(dataset, model)
    text.append("无同卷复评覆盖（按中心）：")
    for centre, people in sorted(gaps.items()):
        text.append(f"  {centre}（{len(people)} 人）：{', '.join(people)}")
    text.append("无锚点考官：" + ", ".join(unanchored_examiners(dataset, model)) or "无")
    text.append("提示：service/oversight 未被共同评阅，跨站套用 common 偏移"
                "属于任务不变性假设，只能在当地解释或按严格口径拒认。")
    _emit({"reference": model.reference_centre,
           "adjustment": model.centre_adjustment,
           "chains": [c.__dict__ for c in model.chains],
           "coverage_gaps": gaps}, args.json, "\n".join(text))


def cmd_headcount(args) -> None:
    dataset, model = load(args.root)
    stats = headcounts(dataset, model, args.as_of)
    rows = [[h.rule, h.task, h.mode, h.treatment, str(h.local_pass),
             str(h.portable_pass),
             ",".join(h.blocked_by_gap) or "-",
             ",".join(h.expired_or_revoked) or "-",
             ",".join(h.flips_vs_local) or "-"] for h in stats]
    text = _table(["规则", "任务", "模式", "处理口径", "本地达标",
                   "本口径可采信", "证据缺口阻断", "到期/撤销阻断",
                   "相对本地翻转"], rows)
    ranges: dict[str, tuple[int, int]] = {}
    for h in stats:
        vals = [x.portable_pass for x in stats if x.rule == h.rule]
        ranges[h.rule] = (min(vals), max(vals))
    text += "\n达标人数范围（严格→本地）：" + "；".join(
        f"{r} {lo}-{hi} 人" for r, (lo, hi) in sorted(ranges.items()))
    _emit([h.__dict__ for h in stats], args.json, text)


def cmd_candidate(args) -> None:
    dataset, model = load(args.root)
    verdicts = [v for v in all_verdicts(dataset, model, args.treatment, args.as_of)
                if v.candidate == args.candidate]
    rows = []
    for v in verdicts:
        b = v.basis
        rows.append([v.rule, v.task, v.mode, "达标" if v.passes else "不达标",
                     str(v.min_score),
                     str(b.raw_score) if b else "-",
                     str(b.score_used) if b else "-",
                     b.tier if b else "-",
                     str(b.expires_day) if b else "-",
                     b.reason if b else "无有效成绩"])
    text = [f"考生 {args.candidate}（口径={args.treatment}，截至第 {args.as_of} 日）",
            _table(["规则", "任务", "模式", "结论", "阈值", "原始分", "采用分",
                    "证据层级", "到期日", "依据/缺口"], rows)]
    paired = "是" if args.candidate in model.paired_candidates else "否"
    text.append(f"本人同卷复评覆盖：{paired}")
    _emit([v.__dict__ for v in verdicts], args.json, "\n".join(text))


def cmd_training(args) -> None:
    dataset, _ = load(args.root)
    text = ["带教经历角色分离（观察日早于培训开始者不得计入训练成效）："]
    rows = []
    for item in classify_periods(dataset):
        p = item.period
        rows.append([p.candidate, p.track, str(p.training_day),
                     str(p.duty_observed_day), str(p.supervised_hours),
                     item.role, item.note])
    text.append(_table(["考生", "训练方向", "培训开始", "观察日", "监督工时",
                        "证据角色", "说明"], rows))
    text.append("\n按人汇总：")
    srows = [[s.candidate, ",".join(s.tracks), str(s.post_training_events),
              str(s.post_training_hours), str(s.pre_observation_events),
              str(s.pre_observation_hours)] for s in summarize(dataset)]
    text.append(_table(["考生", "方向", "成效事件", "成效工时",
                        "观察前事件", "观察前工时"], srows))
    _emit({"summaries": [s.__dict__ for s in summarize(dataset)]},
          args.json, "\n".join(text))


def cmd_priority(args) -> None:
    dataset, model = load(args.root)
    scheduler = Scheduler(dataset, model, args.treatment, args.as_of)
    plan = scheduler.plan()
    blocked = {b.duty for b in plan.unfillable}
    rows_p = review_priority(dataset, model, args.as_of, blocked)
    rows = [[r.candidate, r.home, str(r.score), str(r.score_value),
             "；".join(r.reasons)] for r in rows_p]
    text = _table(["考生", "归属站", "common 分", "优先分", "优先原因"], rows)
    _emit([r.__dict__ for r in rows_p], args.json, text)


def cmd_ingest(args) -> None:
    root = args.root
    batch_dir = Path(args.batch)
    present = validate_batch(batch_dir)
    # 合并后整体校验
    merged_dirs = data_dirs(root) + [batch_dir]
    load_dataset(merged_dirs)
    target = root / "data" / "batches" / batch_dir.name
    if target.exists():
        raise LoadError(f"批次名已存在：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # 复制（同文件系统下 rename 即可；跨设备回退复制）
    try:
        batch_dir.rename(target)
    except OSError:
        import shutil
        shutil.copytree(batch_dir, target)
    journal = Journal(root / "data" / "journal.jsonl")
    entry = journal.append("ingest_batch",
                           {"batch": batch_dir.name, "tables": present})
    print(f"已追加批次 {batch_dir.name}（表：{', '.join(present)}），日志序号 {entry['seq']}")


# ---------------- 排班人员命令 ----------------

def _plan_text(plan) -> str:
    lines = [f"口径={plan.treatment}，截至第 {plan.as_of_day} 日"]
    rows = [[a.duty, a.centre, f"{a.day} {a.start}-{a.end}", a.mode,
             a.candidate, a.supervisor or "-", a.source]
            for a in plan.assignments]
    lines.append(_table(["班次", "站点", "时间", "模式", "候选人", "监督人", "来源"], rows))
    if plan.receipt_findings:
        lines.append("\n既有签收核对（accepted 失效与 reserved 受阻处置不同）：")
        for f in plan.receipt_findings:
            lines.append(f"  [{f.kind}] {f.detail}")
    if plan.unfillable:
        lines.append("\n无法落位的班次：")
        for f in plan.unfillable:
            lines.append(f"  [{f.kind}] {f.duty}: {f.detail}")
    if plan.term_breaches:
        lines.append("\n来源站最低留守能力冲突：")
        for f in plan.term_breaches:
            lines.append(f"  [{f.kind}] {f.detail}")
    if not (plan.receipt_findings or plan.unfillable or plan.term_breaches):
        lines.append("\n全部班次可落位且无约束冲突。")
    return "\n".join(lines)


def cmd_plan(args) -> None:
    dataset, model = load(args.root)
    plan = Scheduler(dataset, model, args.treatment, args.as_of).plan()
    _emit({"treatment": plan.treatment, "as_of": plan.as_of_day,
           "assignments": [a.__dict__ for a in plan.assignments],
           "unfillable": [b.__dict__ for b in plan.unfillable],
           "term_breaches": [b.__dict__ for b in plan.term_breaches],
           "receipt_findings": [b.__dict__ for b in plan.receipt_findings]},
          args.json, _plan_text(plan))


def cmd_verify(args) -> None:
    dataset, model = load(args.root)
    scheduler = Scheduler(dataset, model, args.treatment, args.as_of)
    locked, findings = scheduler.review_receipts()
    lines = [f"截至第 {args.as_of} 日重核 {len(dataset.receipts)} 条签收："]
    for a in locked:
        lines.append(f"  {a.duty} <- {a.candidate}"
                     + (f" 监督 {a.supervisor}" if a.supervisor else "")
                     + f"（{a.source}）")
    if not findings:
        lines.append("所有承诺在当前证据下仍然成立。")
    for f in findings:
        lines.append(f"  [{f.kind}] {f.detail}")
    _emit({"findings": [f.__dict__ for f in findings],
           "locked": [a.__dict__ for a in locked]}, args.json, "\n".join(lines))


def _find_duty(dataset, duty_id: str) -> DutyRequest:
    duty = dataset.duty_by_id(duty_id)
    if duty is None:
        raise LoadError(f"未知班次：{duty_id}")
    return duty


def _append_receipt(root: Path, event: str, duty: DutyRequest, candidate: str,
                    supervisor: str, state: str, as_of: int) -> None:
    ops = root / "data" / "batches" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    path = ops / "duty_receipts.csv"
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        if new_file:
            writer.writerow(["event", "duty", "candidate", "supervisor",
                             "day", "state"])
        writer.writerow([event, duty.duty, candidate, supervisor, as_of, state])


def cmd_submit(args) -> None:
    dataset, model = load(args.root)
    scheduler = Scheduler(dataset, model, args.treatment, args.as_of)
    duty = _find_duty(dataset, args.duty)
    check = scheduler.submit(duty, args.candidate, args.supervisor)
    status = "通过，可确认" if check.ok else "驳回"
    print(f"班次 {args.duty} 派工检查：{status}")
    for r in check.reasons:
        print(f"  - {r}")
    if check.ok:
        basis = check.basis
        sb = check.supervisor_basis
        print(f"  候选人资格依据：{basis.basis.mark_id}（采用分 "
              f"{basis.basis.score_used}/{basis.min_score}，层级 "
              f"{basis.basis.tier}，到期第 {basis.basis.expires_day} 日）")
        if sb and sb.basis:
            print(f"  监督权利依据：{sb.basis.mark_id}（采用分 "
                  f"{sb.basis.score_used}，层级 {sb.basis.tier}）")
    journal = Journal(args.root / "data" / "journal.jsonl")
    journal.append("submit_check",
                   {"duty": args.duty, "candidate": args.candidate,
                    "supervisor": args.supervisor, "ok": check.ok,
                    "reasons": check.reasons})
    if not check.ok:
        sys.exit(1)


def cmd_confirm(args) -> None:
    dataset, model = load(args.root)
    scheduler = Scheduler(dataset, model, args.treatment, args.as_of)
    duty = _find_duty(dataset, args.duty)
    check = scheduler.submit(duty, args.candidate, args.supervisor)
    if not check.ok:
        print("确认被拒：")
        for r in check.reasons:
            print(f"  - {r}")
        sys.exit(1)
    event = args.event or f"E{args.as_of}-{args.duty}-{args.candidate}"
    _append_receipt(args.root, event, duty, args.candidate,
                    args.supervisor, "accepted", args.as_of)
    # 写入后重新加载验证引用与主键
    load(args.root)
    Journal(args.root / "data" / "journal.jsonl").append(
        "confirm", {"event": event, "duty": duty.duty,
                    "candidate": args.candidate, "supervisor": args.supervisor})
    print(f"已确认并留痕：{event}（{duty.duty}，候选人 {args.candidate}"
          + (f"，监督 {args.supervisor}" if args.supervisor else "") + "）")


def cmd_reassign(args) -> None:
    dataset, model = load(args.root)
    scheduler = Scheduler(dataset, model, args.treatment, args.as_of)
    duty = _find_duty(dataset, args.duty)
    locked, _ = scheduler.review_receipts()
    occupied = [a for a in locked if a.duty != duty.duty]
    assignment = scheduler.reassign(duty, args.candidate, args.supervisor, occupied)
    if assignment is None:
        print(f"改派失败：{args.candidate} 不满足 {duty.duty} 的当前约束。")
        sys.exit(1)
    # 留守能力复核
    breaches = scheduler._term_violations(locked + [assignment])
    event = args.event or f"R{args.as_of}-{duty.duty}-{args.candidate}"
    _append_receipt(args.root, event, duty, args.candidate,
                    args.supervisor, "reassigned", args.as_of)
    load(args.root)
    Journal(args.root / "data" / "journal.jsonl").append(
        "reassign", {"event": event, "duty": duty.duty,
                     "candidate": args.candidate, "supervisor": args.supervisor,
                     "term_breaches": [b.detail for b in breaches]})
    print(f"已改派并留痕：{event} -> {args.candidate}"
          + (f"（监督 {args.supervisor}）" if args.supervisor else ""))
    if breaches:
        print("注意：改派后存在留守能力冲突，需继续调整：")
        for b in breaches:
            print(f"  - {b.detail}")


def cmd_audit_trail(args) -> None:
    journal = Journal(args.root / "data" / "journal.jsonl")
    entries = journal.read()
    if args.action:
        entries = [e for e in entries if e["action"] == args.action]
    rows = [[str(e["seq"]), e["action"],
             json.dumps({k: v for k, v in e.items() if k not in ("seq", "action")},
                        ensure_ascii=False)] for e in entries]
    print(_table(["序号", "动作", "内容"], rows))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="跨站考评校准与带教释放决策")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="项目根目录（默认仓库根）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("equating", help="共同评阅锚定与漂移分析")
    p.set_defaults(func=cmd_equating)

    p = sub.add_parser("headcount", help="三种处理下的达标人数范围")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_headcount)

    p = sub.add_parser("candidate", help="单个考生的资格证据卡")
    p.add_argument("candidate")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_candidate)

    p = sub.add_parser("training", help="训练成效与观察前经历分离")
    p.set_defaults(func=cmd_training)

    p = sub.add_parser("priority", help="下一轮共同评阅优先名单")
    p.add_argument("--treatment", choices=TREATMENTS, default="strict")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_priority)

    p = sub.add_parser("ingest", help="校验并追加一个机构考核/班次批次目录")
    p.add_argument("batch", help="待追加批次目录路径")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("plan", help="排班方案与约束缺口")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("verify", help="按当前证据重核既有签收/承诺")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("submit", help="提交班次方案做派工检查（不留确认）")
    p.add_argument("duty")
    p.add_argument("candidate")
    p.add_argument("--supervisor", default="")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("confirm", help="确认班次并写入可追查签收")
    p.add_argument("duty")
    p.add_argument("candidate")
    p.add_argument("--supervisor", default="")
    p.add_argument("--event", default="")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("reassign", help="失效班次改派并留痕")
    p.add_argument("duty")
    p.add_argument("candidate")
    p.add_argument("--supervisor", default="")
    p.add_argument("--event", default="")
    p.add_argument("--treatment", choices=TREATMENTS, default="calibrated")
    p.add_argument("--as-of", type=int, default=29)
    p.set_defaults(func=cmd_reassign)

    p = sub.add_parser("audit-trail", help="查询追加式操作日志")
    p.add_argument("--action", default="", choices=["", "ingest_batch", "submit_check",
                                                     "confirm", "reassign"])
    p.set_defaults(func=cmd_audit_trail)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except LoadError as exc:
        print(f"资料错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
