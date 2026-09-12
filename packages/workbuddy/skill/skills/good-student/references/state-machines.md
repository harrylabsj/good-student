# Good-student 状态机

## 候选题

`received → extracting → needs_confirmation → confirmed → analyzed → archived`

候选也可能进入 `rejected`、`expired` 或 `extraction_failed`。只有 `confirmed` 可以进入分析。

## 知识点判断

`insufficient_evidence → suspected_weakness → evidenced_weakness → improving → mastered`

掌握状态在时间衰减或出现新错误后进入 `review_due`。少于两条独立证据时置信度必须为 low；计划完成本身不改变能力状态，只有新变式、无提示复测才改变状态。
