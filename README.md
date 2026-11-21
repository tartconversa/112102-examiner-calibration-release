# 跨站培训评分校准与带教释放决策

跨站考核、共同复评、人员可用和监督派班的合成记录。 所有标识、金额、地名和观察值均为合成数据。

## 来源资料

- `sources/marks.csv`
- `sources/training_periods.csv`
- `sources/qualification_rules.csv`
- `sources/duty_requests.csv`
- `sources/release_terms.csv`
- `sources/availability.csv`
- `sources/duty_receipts.csv`

marks 的 common 是统一任务，两个机构对相同 candidate 的 common 记录为分别对同一作业的评阅。不论是否补考，不同 task 都按不同能力口径处理。known_day 是资料到达日，revokes 引用被撤销的原始记录。

training_periods 的 training_day 表示培训开始，duty_observed_day 为观测现场经历的日期。qualification_rules 的 validity_days 从评阅当日起算。

availability 是整日可被安排的区间，release_terms 保存来源站的最低留守人力，duty_requests 的 mode 是班次要求。派班方收到的签收与训练方撤销成绩是独立传输。

## 本地检查

使用 Python 3.14，仅需标准库。在项目目录执行以下命令检查输入表结构、事件引用和基础记录覆盖。

```sh
python -m unittest discover -s tests -v
```
