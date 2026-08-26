# Good-student

面向家长和学生的「错题诊断与学习行动」产品：**Plugin + Skill + 确定性核心**。

- 宿主模型负责理解（识别错题），Good-student 负责可信（校验、确认、分析、复测闭环）。
- 模型输出先进入候选区，人工确认后才写入正式记录。
- 未成年人学习数据 local-first：默认只存本机，可导出、可删除。
- 错题本只能产生「疑似薄弱点」；掌握提升必须由新变式复测证明。

当前进度：**M0（协议与可信底座）已完成**；**M1（Hermes 垂直切片）基本完成**——插件已在真实 Hermes v0.20.5 装配验证（`hermes plugins doctor --ci` 通过、12 工具注册），端到端验收在真实宿主模型上跑通三科 20 题全闭环（证据：`docs/designs/2026-08-25-m1-e2e-evidence.md`），旧数据迁移器、prompt/Skill 定稿、评测框架（56 题五科合成样本 + `scripts/check_eval_gate.py` D5 门禁）均落地。**M1 剩余出口：用真实匿名错题材料解除 D5 门禁（当前真实材料 0 题，合成样本不计入达标）**。设计与计划见 `docs/designs/`。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,mcp]"

# 跑测试
.venv/bin/pytest

# 体验 M0 闭环（手工候选 JSON → 确认 → 分析 → 计划 → 复测）
.venv/bin/good-student create-student 小明 --grade 五年级
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
.venv/bin/python -m mcp_server_good_student  # 或见 mcp/server.py
```

## 仓库结构

`core/good_student/` 确定性核心（Schema 校验、SQLite 存储、规则分析、建议引擎、服务层），
`schemas/` 三份 JSON Schema 是唯一协议源，`prompts/` 宿主模型提取指令，
`skills/good-student/` 跨宿主共享 Skill，`mcp/server.py` stdio MCP 服务，
`packages/` 各宿主适配（M1/M2 填充）。核心不依赖任何宿主 SDK。

## 隐私

本地 SQLite（WAL），默认数据目录 `~/.good-student/`（可用 `GOOD_STUDENT_DATA` 覆盖）。
不复制原始附件，只保存宿主引用与必要摘录；日志不落完整题目与答案。
