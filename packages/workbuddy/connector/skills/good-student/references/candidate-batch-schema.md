# CandidateWrongQuestionBatch v1 协议

调用 `good-student ingest <student_id> <batch.json>` 前，JSON 根对象只能包含
`schema_version`、`source`、`questions` 和可选的 `warnings`。`schema_version` 必须为 `1`。

- `source` 必含 `source_type`（`image`、`pdf`、`text`、`markdown`、`csv` 或 `json`）和 1–512 字符的 `source_ref`；可选 `page_count` 为 1–1000 的整数。
- `questions` 为 1–500 项数组。每题必含 `source_locator`（1–128 字符）、`question_text`（1–20000 字符）和布尔值 `is_wrong`。
- 可选 `subject`、`grade` 的格式是 `{"value": "文本", "confidence": 0 到 1}`；`value` 长度 1–64。
- `student_answer`、`correct_answer` 可为字符串或 `null`，最大 8000 字符。
- `knowledge_candidates` 最多 8 项，每项为 `{"label": "1–128 字符", "confidence": 0 到 1}`。
- `error_reason_candidates` 最多 4 项，`code` 只能是 `concept_gap`、`procedure_gap`、`misread`、`calculation`、`memory`、`careless_checking` 或 `unknown`；可选 `confidence` 为 0 到 1。
- 可选 `extraction_confidence` 为 0 到 1；`needs_confirmation` 为布尔值；`uncertain_fields` 最多 10 项，只能填写字段名。

字段不确定时可省略可选字段或填 `null`（仅答案字段），并在 `uncertain_fields` 中标明；不得加入协议外字段。完整有效输入见同目录 `candidate-batch.example.json`。
