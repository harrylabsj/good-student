# Good-student

面向家长和学生的「错题诊断与学习行动」产品：**Plugin + Skill + 确定性核心**。

- 宿主模型负责理解（识别错题），Good-student 负责可信（校验、确认、分析、复测闭环）。
- 模型输出先进入候选区，人工确认后才写入正式记录。
- 未成年人学习数据 local-first：默认只存本机，可导出、可删除。
- 错题本只能产生「疑似薄弱点」；掌握提升必须由新变式复测证明。

当前进度：**M0（协议与可信底座）已完成**；**M1（Hermes 垂直切片）基本完成**——插件已在真实 Hermes v0.20.5 装配验证，端到端验收在真实宿主模型上跑通三科 20 题全闭环（证据：`docs/designs/2026-08-25-m1-e2e-evidence.md`），旧数据迁移器、prompt/Skill 定稿、评测框架（56 题五科合成样本 + 可插拔 extractor 指标）均落地。现已支持结构化存储、筛选和汇总学生的多科成绩。**2026-09-14 起 WorkBuddy 首发改为独立专家包**（内嵌 Skill、stdio MCP、托管 Python 安装配置和核心 wheel，无需 PyPI 发布、无需 uvx）；原 D5 机械门禁已移除，识别质量参考线保留在 `evals/README.md`。设计与计划见 `docs/designs/`。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,mcp]"

# 跑测试
.venv/bin/pytest

# 体验 M0 闭环（手工候选 JSON → 确认 → 分析 → 计划 → 复测）
.venv/bin/good-student create-student 小明 --grade 五年级
.venv/bin/good-student record-scores <student-id> examples/scores.json
.venv/bin/good-student list-scores <student-id> --subject 数学 --term 2026-2027-1
.venv/bin/good-student ingest <student-id> examples/batch.json
.venv/bin/good-student list-pending <student-id>
.venv/bin/good-student confirm <student-id> --all
.venv/bin/good-student analyze <student-id>
.venv/bin/good-student plan <student-id>

# 迁移旧 student-companion-agent 数据（只读，先 dry-run）
.venv/bin/good-student migrate-legacy ~/.local/share/student-companion-agent --dry-run

# 评测提取质量（占位样本 + 可插拔 extractor）
.venv/bin/python scripts/evaluate_extraction.py evals/samples

# 启动 MCP server（stdio）
.venv/bin/good-student-mcp  # 或使用 python mcp/server.py

# 构建 WorkBuddy 上传包
.venv/bin/python scripts/build_workbuddy_packages.py
```

## 仓库结构

`core/good_student/` 确定性核心（Schema 校验、SQLite 存储、规则分析、建议引擎、服务层），
`schemas/` 三份 JSON Schema 是唯一协议源，`prompts/` 宿主模型提取指令，
`skills/good-student/` 跨宿主共享 Skill，`mcp/server.py` stdio MCP 服务，
`packages/` 各宿主适配（M1/M2 填充）。核心不依赖任何宿主 SDK。

## 隐私

本地 SQLite（WAL），默认数据目录 `~/.good-student/`（可用 `GOOD_STUDENT_DATA` 覆盖）。
不复制原始附件，只保存宿主引用与必要摘录；日志不落完整题目与答案。

## 结构化成绩

`record-scores` 接受 JSON 数组并原子写入（任一条非法则整批不写入）。每条记录必须包含
`subject`、`assessment_name`、`assessed_at`，并至少提供 `score` 或 `grade_label`；还可记录
`max_score`、考试类型、学期、班级排名、年级排名、备注和来源引用。提供
`score + max_score` 时会自动计算百分比。

## WorkBuddy 发布准备

`packages/workbuddy/` 提供 WorkBuddy 的独立专家包、Skill、兼容连接器和 Buddy 应用配置草案。
专家包内嵌 Skill、本地 stdio MCP、托管 Python 安装配置与核心 wheel；召唤时无需依赖市场
连接器。**无需发布 PyPI、无需 uvx**。按 `docs/workbuddy-publishing-plan.md` 完成本地预览与平台提交。
默认仍是本地 SQLite，不申请 OAuth，不把真实学生材料或密钥打进上传包。隐私说明见
`docs/privacy/minor-data-protection.md`。
