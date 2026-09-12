# Good-student 状态机

只有 `confirmed` 候选可以进入 `analyze`。`rejected`、`expired` 和提取失败不得改变学习画像。

知识点状态从 `insufficient_evidence` 逐步进入 `suspected_weakness`、`evidenced_weakness`、`improving`、`mastered`；时间衰减或新错误会进入 `review_due`。只有新变式且无提示的复测才推进状态。
