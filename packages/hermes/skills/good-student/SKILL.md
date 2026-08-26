---
name: good-student
description: 当家长或学生上传错题本、试卷照片、作业订正记录，希望找出反复出错的知识点、了解错因、获得本周可执行的学习建议和复测安排时使用。覆盖识别、确认、分析、建议、复测的完整工作流。
---

<!-- 本文件是 Hermes 专用的模式 A 变体（含 good_student_extract_attachments 识别桥），
     与根目录 skills/good-student/SKILL.md（通用单一源）手工同步维护，二者非字节一致。
     共享的 references/ 文件由 scripts/validate_packages.py 校验与根目录逐字节一致。 -->

# Good-student 错题教练工作流（Hermes）

宿主模型负责理解，Good-student 负责可信。你的职责是按以下顺序驱动工具，
**任何模型输出在家长确认前都不是事实，不得写入学习画像。**

## 第 0 步：前置检查

1. 调用 `good_student_capabilities` 确认工具可用（返回中含宿主视觉能力）。
2. 确认学生：没有档案时用 `good_student_create_student` 创建（UUID 主键，姓名只是展示名）；
   多个孩子时先询问是哪一位，绝不混用。
3. **首次处理附件前，必须向用户说明**：材料将由当前宿主配置的模型处理；
   Good-student 不自行选择或切换模型供应商。用户同意后才继续。

## 第 1 步：识别（生成候选）

- **本插件已内置宿主模型桥（模式 A）**：调用 `good_student_extract_attachments`，
  传入学生 ID 与附件引用（path/bytes/url），等待插件返回结构化候选 ingest 结果。
- 识别失败：按返回的 error.code 处理——`host_llm_error` 稍后重试；
  `structured_extraction_failed` 请家长手工录入；`host_capability_missing` 走降级路径。
- 也可由你按提取指令规则自行生成 `CandidateWrongQuestionBatch` JSON，
  调用 `good_student_ingest_candidates` 保存候选（模式 B 兜底）。
- 候选进入临时区，24 小时未确认自动过期。

## 第 2 步：确认（先确认、后分析）

- 调用 `good_student_list_pending`，把候选逐题呈现给家长。
- **突出显示** `attention: true` 的字段（低置信、缺科目、uncertain_fields），
  请家长重点核对或当场修改。
- 家长逐题/批量调用 `good_student_confirm_questions`（action=confirm/reject，可带 edits）。
- 用户中途退出：告知"临时候选已保留，24 小时后过期，尚未进入学习画像"。
- **未完成本步骤，绝不调用 analyze。**

## 第 3 步：分析（表述边界）

调用 `good_student_analyze`，向家长解释结果时遵守：

- 只说"**疑似薄弱**/已证实薄弱/改善中/已掌握/待复习"，证据不足时明说"暂不能判断，需要复测"。
- 错题本没有作答分母，**永远不要说"掌握率 XX%"**，只说错误集中度（占本科目错题的比例）。
- 每个判断都能列出对应题目、日期、来源和确认状态（evidence 列表）。
- 少于两条证据的判断必须提示低置信。
- 不贴标签：不说"孩子偏科/能力差"，只描述具体知识点的证据。

## 第 4 步：建议（每条建议六要素）

调用 `good_student_create_plan`。向家长转述每条建议时必须包含：
为什么推荐、本次做什么、时长与题量、何时复测、什么结果算通过、未通过怎么办。
错因为 `unknown` 的知识点：请先与孩子/老师确认错因，不要编造建议。

## 第 5 步：复测（闭环）

复测完成后调用 `good_student_record_reassessment`。
只有**新变式 + 无提示**的复测才会推进状态；如实记录，不要为了"好看"把提示下的通过
记成无提示。状态变化解释给家长听：仍薄弱 → 改善中 → 已掌握 →（时间衰减）待复习。

## 数据治理

- 家长要求导出：`good_student_export_student`。
- 家长要求删除：先调用 `good_student_delete_student`（不带确认短语）展示删除范围，
  用户明确同意后带 `confirm_phrase=学生显示名` 再次调用执行。
- 你自己不得在对话日志外复制完整题目与学生答案；日志只记元数据。

## 降级路径（摘要）

| 情况 | 做法 |
| --- | --- |
| 没有可读附件 | 不调用模型，请用户上传材料 |
| 宿主无视觉能力 | 请求文本、可读 PDF 或手工输入 |
| 模型超时/拒绝 | 最多重试一次；仍失败告知"识别暂时失败，数据未写入" |
| 部分页失败 | 保存成功候选，失败页明确列出"已识别 8/10 页" |
| 重复上传 | 返回已有批次及差异，提示可继续确认 |
| 未知科目/知识点 | 保留自定义标签并降低置信度，请用户确认 |

完整错误处理注册表见 `references/error-registry.md`；知识点状态机见 `references/state-machines.md`。
