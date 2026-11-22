# 批次追加模板

新机构考核或班次资料以“批次目录”形式提交，每个目录只放需要追加的同名
CSV（表头必须与 `sources/` 中对应文件完全一致）：

- `marks.csv`、`training_periods.csv`、`qualification_rules.csv`
- `duty_requests.csv`、`release_terms.csv`、`availability.csv`、`duty_receipts.csv`

校验并入库：

```sh
python -m app.cli ingest /path/to/批次目录
```

入库时会先校验批次本身，再与全部已有资料合并做整体校验（主键冲突、
撤销引用、跨表外键、时间窗合法性）。通过后批次移动到
`data/batches/<批次名>/`，并在 `data/journal.jsonl` 追加一条
`ingest_batch` 留痕；校验失败则原目录不动、项目数据不变。

注意：

- 撤销通知的 `score` 留空，`revokes` 引用被撤销的原记录，撤销按
  `known_day` 生效，可通过 `--as-of` 重放历史时点。
- 同卷复评仍使用 `task=common`：同一考生的两条 common 记录视为对同一
  作业的分别评阅，引擎自动配对生成锚点。
- 运行期数据目录 `data/` 已在 `.gitignore` 中，基础资料 `sources/` 不会被修改。
