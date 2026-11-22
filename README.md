# 跨站培训评分校准与带教释放决策

跨站考核、共同复评、人员可用和监督派班的合成记录，以及基于这些记录的本地
能力分析与排班应用。所有标识、地名和观察值均为合成数据。

## 业务问题与处理原则

两家训练机构（中心 A、B）使用不同评分表：B 站同卷复评分数系统性高于 A 站，
且考官交接后可能出现分数偏移。考生会带原考记录跨站补考。应用坚持以下原则：

1. **只有共同评过的任务（`common`）能支撑分数比较。** 同卷复评按考官交接
   链路配对，输出每条链路的偏移、离散度（漂移告警）和分数支撑区间。
2. **不因为某些水平缺少共同任务就把两家分数视作可直接替换。** 无锚点考官、
   未覆盖考生、超出支撑区间的外推都显式标注；`service`/`oversight` 从未被
   共同评阅，跨站套用 common 偏移属于任务不变性假设，严格口径直接拒认。
3. **三种评分处理给出达标人数范围与证据缺口**：
   - `local` 本地口径：按原站分数判定，仅在当地可解释，不可跨站直接替换；
   - `calibrated` 校准口径：用同卷复评偏移折算，区间外标注外推；
   - `strict` 严格口径：只有锚点直接覆盖（anchored）的成绩才能跨站采信，
     无共同评阅证据者只能补评或有监督实习，不得独立值班。
4. **有效期与撤销按规则执行。** 有效期从评阅当日起算；撤销按资料到达日
   （`known_day`）生效，晚到撤销可用 `--as-of` 重放。被撤销资格的成绩
   不能当作有效补考；撤销通知行本身不是成绩。
5. **带教经历角色互斥。** 观察日早于培训开始日的经历只能作观察前能力依据，
   不计入训练成效；同一次经历不会同时进入两个证据池。
6. **排班约束全部显式校验，缺口上报而不是静默放行**：
   - 独立班需独立资格（K1 类），监督班需监督实习资格（K3 类），监督人需
     监督权利（K2 类），各任务有独立阈值与有效期；
   - 同一人（任何角色）不得同时承担两处时间重叠的承诺；
   - 跨站释放不得使来源站在 `release_terms` 窗口的在岗独立能力人数低于
     `min_independent`；
   - `accepted` 是正式承诺、`reserved` 只是暂定：复核使资格失效时，正式
     承诺解除锁定并要求及时改派，暂定承诺直接标记受阻，二者处置不同；
   - 所有提交、确认、改派、批次追加都写入只追加的 `data/journal.jsonl`，
     已收到的确认始终可追查。

## 来源资料（不可变基础数据）

- `sources/marks.csv`：考评记录。`common` 为统一任务，同一考生的两条
  common 记录是对同一作业的分别评阅；`revokes` 引用被撤销的原记录，
  `known_day` 是资料到达日。
- `sources/training_periods.csv`：`training_day` 培训开始，
  `duty_observed_day` 现场观察日期。
- `sources/qualification_rules.csv`：阈值、有效期（自评阅日起）与模式。
- `sources/duty_requests.csv`：班次需求，`mode` 为独立/监督班。
- `sources/release_terms.csv`：来源站最低留守独立能力。
- `sources/availability.csv`：人员整日可安排区间。
- `sources/duty_receipts.csv`：派班方签收，与训练方撤销独立传输。

## 应用结构（仅标准库，Python 3.14）

| 模块 | 职责 |
| --- | --- |
| `app/loader.py` | 多目录加载、表头/类型/引用/撤销/主键校验、批次入库 |
| `app/equate.py` | 同卷复评配对、考官链路偏移与漂移、证据层级判定 |
| `app/qualifications.py` | 三种处理下的资格判定、有效期、撤销重放、人数范围 |
| `app/training.py` | 训练成效与观察前依据的互斥分离 |
| `app/scheduling.py` | 资格/时间窗/重叠/留守约束、签收核对、规划与改派 |
| `app/priority.py` | 下一轮共同评阅优先名单 |
| `app/audit.py` | 只追加的 JSONL 审计日志 |

## 常用命令

所有命令支持 `--json` 输出机器可读结果，`--as-of N` 重放到第 N 日的证据状态。

训练负责人：

```sh
python -m app.cli equating                 # 锚定链路、偏移、漂移、覆盖缺口
python -m app.cli headcount                # 三种处理下各规则达标人数范围
python -m app.cli candidate P15            # 单人资格证据卡（口径/层级/到期/缺口）
python -m app.cli training                 # 训练成效 vs 观察前经历
python -m app.cli priority                 # 下一轮共同评阅优先覆盖谁
python -m app.cli ingest /path/to/批次目录  # 校验并追加机构考核/班次资料
```

排班人员：

```sh
python -m app.cli plan                     # 排班方案、无法落位班次、留守冲突
python -m app.cli verify                   # 按当前证据重核既有签收/承诺
python -m app.cli submit D2 P33 --supervisor P21   # 派工检查（不写确认）
python -m app.cli confirm D4 P8 --event E9         # 确认并写入可追查签收
python -m app.cli reassign D2 P33 --supervisor P15 # 失效班次改派并留痕
python -m app.cli audit-trail                       # 查询每次派工背后的留痕
```

`submit` 不通过时退出码为 1 并列出全部阻断原因；`confirm`/`reassign` 写入
`data/batches/ops/duty_receipts.csv` 后会重新加载做整体校验，拒绝写入
破坏引用或主键的数据。

## 本地检查

```sh
python -m unittest discover -s tests -v
```

34 个测试覆盖：加载校验与批次冲突、锚定配对与漂移检测、三种口径的边界
翻转与证据层级、撤销的晚到重放、有效期、训练经历互斥、监督人双重工时、
跨站独立值班的锚点要求、留守能力冲突、失效改派。
