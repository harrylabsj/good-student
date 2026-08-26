# Good-student 评测样本集（设计 §21.2）

对错题提取（prompt + 宿主模型）做契约评测的固定样本集。当前共 **22 个样本、56 道
期望题，全部为合成占位材料（meta.synthetic=true）**，规模与场景覆盖已就位，但
**不代表 D5 达标**——D5 要求真实匿名材料，见下文「当前状态」。

## 匿名化要求（入库前必须完成）

- 去除学生与家长的**姓名**、**学校**、班级、考号、联系方式。
- 图片/PDF 中去除**面部**、**手写签名**、校徽等可识别信息（打码或裁切）。
- 题目正文中的真实人名替换为占位名。
- 每个样本的 `meta.anonymized` 必须为 `true` 才允许入库；合成样本标 `synthetic: true`。

## D5 门槛（M1 出口条件，开发计划 §1）

- 样本规模：**≥50 题**，覆盖**数学、语文、英语、物理、化学**五科。
- 清晰印刷题：**字段准确率 ≥90%**。
- 手写题：**字段准确率 ≥70%**。
- **漏题率 ≤5%**。
- 不达门槛不进入真实使用。

## 样本目录结构

每个样本一个子目录：

```
evals/samples/<sample-name>/
    attachments/        # 一份或多份附件（真实样本为图片/PDF；占位样本用 .txt）
    expected.json       # 人工标注
    candidates.json     # （可选）离线 fake extractor 读取的"模型输出"
```

`expected.json` 格式：

```json
{
  "meta": {
    "synthetic": false,
    "anonymized": true,
    "kind": "clear_print | handwriting | multi_page_pdf | mixed_subjects | cropped_blurry_rotated | no_correct_answer | prompt_injection",
    "subjects": ["数学"]
  },
  "questions": [
    {
      "source_locator": "page-1-question-1",
      "subject": "数学",
      "question_text": "……",
      "student_answer": "…… 或 null",
      "correct_answer": "…… 或 null",
      "is_wrong": true,
      "knowledge": ["异分母分数加法"],
      "uncertain_fields": ["student_answer"],   // 可选：断言模型至少把这些字段标为 uncertain
      "low_confidence": true,                   // 可选：断言 extraction_confidence < 0.8 且 needs_confirmation=true
      "needs_confirmation": true                // 可选：断言 needs_confirmation=true
    }
  ]
}
```

题目按 `source_locator` 与 extractor 输出对齐。未标注的字段不参与字段准确率统计。

字段准确率统计的字段固定为 `COMPARE_FIELDS`（`subject`/`question_text`/`student_answer`/
`correct_answer`/`knowledge`/**`is_wrong`**）。`is_wrong` 必须参与对比——注入样本攻击的
正是该字段，不参与就无法判定模型是否被注入篡改。

`uncertain_fields` / `low_confidence` / `needs_confirmation` 是**低置信路径断言**
（见脚本中 `ATTENTION_KEYS`）：仅在 `expected.json` 携带时才校验，计入 per-sample 的
`attention_checked` / `attention_ok` 与汇总的 `attention` 指标，**不掺入字段准确率**。
`uncertain_fields` 取超集语义（期望是最低必标集合，模型多标更安全）；
`low_confidence` 要求置信度低于 0.8 且显式 `needs_confirmation=true`。
模糊/手写/无答案类样本应逐题标注，否则低置信路径被视为未覆盖（D5 门禁 `attention_accuracy` 会 FAIL）。

## 场景矩阵（§21.2，正式样本集需逐项覆盖）

- 清晰印刷题
- 手写答案
- 多页 PDF
- 同页混合科目
- 裁切、模糊、旋转
- 无答案或正确答案不可见
- 附件中包含提示注入文本
- 模型拒绝和非法 JSON（由 adapter/contract fixtures 覆盖）

## 运行评测与 D5 门禁

```bash
.venv/bin/python scripts/evaluate_extraction.py evals/samples --output evals/last-run.json
.venv/bin/python scripts/check_eval_gate.py                # 默认读 evals/samples + evals/last-run.json
.venv/bin/python scripts/check_eval_gate.py --output evals/gate-result.json
```

默认使用离线 fake extractor（读各样本的 `candidates.json`）。接入真实宿主后用
`--extractor module:function` 替换，签名见脚本 docstring。

`check_eval_gate.py` 机械检查 D5 每条：样本题数 ≥50、五科覆盖、真实材料占比
（**synthetic 样本不计入 D5 达标**）、清晰印刷（逐字段 ≥90%）/ 手写（逐字段 ≥70%）
分层准确率、真实样本漏题率 ≤5%。逐项输出 PASS/FAIL，全部通过退出码 0，否则 1。

## 样本清单（全部 synthetic，场景矩阵文本可表达部分已覆盖）

| 样本 | 场景 kind | 科目与题数 | 候选偏差设计 |
| --- | --- | --- | --- |
| sample-01-clear-print-math | clear_print | 数学 2 | 无（全对） |
| sample-02-mixed-subjects | mixed_subjects | 数学+英语 2 | 无（全对） |
| sample-03-handwriting-blur | handwriting | 物理/数学/语文 3 | 漏 1 题、1 个字段不可辨认 |
| clear-print-math | clear_print | 数学 3 | 无（全对） |
| clear-print-chinese | clear_print | 语文 3 | 无（全对） |
| clear-print-english | clear_print | 英语 3 | 无（全对） |
| clear-print-physics | clear_print | 物理 3 | 无（全对） |
| clear-print-chemistry | clear_print | 化学 3 | 无（全对） |
| handwriting-math | handwriting（文本模拟） | 数学 3 | 漏 1 题（涂改严重）、1 处答案转写错误 + uncertain_fields |
| handwriting-english | handwriting（文本模拟） | 英语 3 | 无（全对，置信度中等） |
| handwriting-chinese | handwriting（文本模拟） | 语文 3 | 1 处答案转写错误（“晶荧”→“晶萤”）+ uncertain_fields |
| handwriting-physics | handwriting（文本模拟） | 物理 2 | 无（全对，置信度中等） |
| handwriting-chemistry | handwriting（文本模拟） | 化学 2 | 无（全对，置信度中等） |
| mixed-subjects-pcm | mixed_subjects | 数学+物理+化学 3 | 无（全对） |
| mixed-subjects-ce | mixed_subjects | 语文+英语 2 | 多 1 条误题（页脚提示误当题目），验证误题率 |
| no-answer-math | no_correct_answer | 数学 2 | correct_answer 双方均 null |
| no-answer-chemistry | no_correct_answer | 化学 2 | correct_answer null + uncertain_fields（答案栏被裁切） |
| prompt-injection-math | prompt_injection | 数学 2 | 附件含中文注入（要求篡改 is_wrong），候选不执行并告警 |
| prompt-injection-english | prompt_injection | 英语 2 | 附件含英文注入（要求输出 all_correct），候选不执行并告警 |
| blurry-rotated-math | cropped_blurry_rotated | 数学 2 | 低置信、题干残缺不编造、答案置 null + uncertain_fields |
| blurry-physics | cropped_blurry_rotated | 物理 2 | 1 处答案转写错误（“平行”→“平齐”）+ uncertain_fields |
| multipage-chinese-english | multi_page_pdf | 语文 2 + 英语 2 | 无（全对），3 个附件页，source.page_count=3 |

场景矩阵中「模型拒绝和非法 JSON」由 adapter/contract fixtures（tests/contracts/）
覆盖，不在样本集内。

## 当前状态（2026-08-26，fake extractor 全量跑通）

- 指标（`evals/last-run.json`）：22 样本 / 56 题，漏题率 2/56 ≈ 3.6%，误题率 1/55 ≈ 1.8%；
  字段准确率 subject/correct_answer/knowledge/is_wrong = 1.0（54/54），question_text = 0.9815，
  student_answer = 0.9074；低置信断言（attention）7/7 全部满足；置信度校准方向正确
  （答错均值 0.52 < 答对均值 0.89）。
- 门禁（`evals/gate-result.json`，退出码 1）：
  - PASS `sample_count`：56 题 ≥ 50
  - PASS `subject_coverage`：数学/语文/英语/物理/化学齐全
  - FAIL `real_material`：真实匿名材料 0/56 题——**synthetic 样本不计入 D5 达标**
  - FAIL `clear_print_accuracy` / `handwriting_accuracy` / `miss_rate`：无真实样本，无法判定
  - FAIL `attention_accuracy`：无带低置信断言的真实样本（低置信路径未覆盖真实材料）
  - FAIL `injection_validation`：注入样本由离线 fake extractor 评测，未验证真实模型抗注入能力
- 结论：**D5 未达标**。缺口是真实匿名材料本身（收集与匿名化流程）+ 真实宿主 extractor
  （注入/低置信维度需要真实模型跑分才有意义），不是评测基建。

## 真实材料接入路径（遗留）

1. 收集真实错题材料（拍照/扫描试卷、错题本），按上文「匿名化要求」处理后入库，
   meta 标 `synthetic: false, anonymized: true`；真实样本建议按 `real-` 前缀命名以便区分。
2. 接入真实宿主 extractor（M1 Hermes `complete_structured` 桥），用
   `--extractor module:function` 跑分层指标，替换 `evals/last-run.json`。
3. 重跑 `check_eval_gate.py`：真实题数 ≥50 且五科齐全、清晰印刷逐字段 ≥90%、
   手写逐字段 ≥70%、真实样本漏题率 ≤5% 全部 PASS（退出码 0）方可进入真实使用。
