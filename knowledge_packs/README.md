# knowledge_packs — K12 知识点种子包

每个文件是一个学科的知识点包，格式：

```json
{
  "pack_id": "k12-math",
  "version": 1,
  "subject_id": "数学",
  "components": [
    {
      "id": "math.fraction.wordproblems",
      "canonical_name": "分数应用题",
      "aliases": ["分数解决问题"],
      "grade_range": "五年级-六年级",
      "prerequisite_ids": ["math.fraction.operations"]
    }
  ]
}
```

规则：

- `id` 为稳定 slug，只在本包内唯一；`prerequisite_ids` 只能引用本包内的 `id`。
- `canonical_name` / `aliases` 用于把宿主模型识别出的自由文本知识点归一到标准节点；
  归一成功后知识组件 `is_custom=false`，并携带年级范围与前置知识链。
- 种子包是课程先验，不是事实断言：薄弱点判断仍然只来自已确认的题目级证据；
  前置链仅用于给出"根子可能在哪"的提示。
- 通过 `GOOD_STUDENT_KNOWLEDGE_DIR` 环境变量可替换为自定义包目录（同名文件覆盖内置包）。

加载器见 `core/good_student/knowledge.py`；构建 wheel 时本目录映射为
`good_student.knowledge_packs` 子包并随包分发。
