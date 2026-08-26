---
title: Good-student 开发计划
status: proposed
version: 1.0
date: 2026-08-23
based-on: Good-student 产品与系统设计 v1.0
---

# Good-student 开发计划

## 0. 现状盘点

- 设计文档 v1.0（proposed）已完成，覆盖架构、数据模型、工具合约、状态机、错误降级、测试策略与分阶段路线图（Phase 0–3）。
- 旧项目 `~/coding/student-companion-agent`：单脚本 CLI（`scripts/student_companion.py`）+ `SKILL.md` + `references/data-schema.md`，按设计 §22 作为 0.x 数据源做**只读迁移**，不原地改造。
- `good-student` 为全新仓库，按设计 §17 的目录结构建仓。

## 1. 开工前需拍板的工程决策（设计 §28，附建议）

| # | 决策 | 建议 | 影响 |
|---|---|---|---|
| D1 | Core 形态 | Python package `good_student` + 薄 stdio MCP server **双形态**。§17 仓库结构已含 `mcp/server.py`；Hermes 走 Python API，OpenClaw/Codex 走 MCP，共用同一核心 | M0 的交付物清单 |
| D2 | OpenClaw 首版 | 先做兼容 Agent Plugins bundle（模式 B），原生 adapter 延后到确有媒体能力需求时 | M2 范围 |
| D3 | 跨宿主数据 | 默认**每宿主独立数据目录**（`PLUGIN_DATA`），单写者原则；跨宿主迁移靠 export/import 工具，不做共享目录并发写 | 存储层与 doctor 设计 |
| D4 | 存储引擎 | SQLite（WAL、单文件、原子提交）+ JSONL 事件日志（仅元数据）；export 输出 JSON；doctor 做完整性检查。理由：设计 §19 要求处理锁失败/磁盘满/损坏恢复，SQLite 原生满足；迁移用版本表管理 | M0 任务 3 |
| D5 | 评测门槛 | 匿名样本集 ≥50 题、覆盖数学/语文/英语/物理/化学；清晰印刷题字段准确率 ≥90%、手写 ≥70%、漏题率 ≤5% 才进入真实使用 | M1 出口条件 |

## 2. 里程碑总览

单人全职 + agent 辅助的粗估，用于排期与依赖，不做承诺。

| 里程碑 | 内容 | 粗估 | 出口条件 |
|---|---|---|---|
| M0 = Phase 0 | 协议与可信底座（纯 core，不接宿主模型） | ~2 周 | 手工候选 JSON 走完确认→分析→计划→复测闭环 |
| M1 = Phase 1 | Hermes 垂直切片 + 旧数据迁移器 | ~2 周 | Hermes 上传错题本零新 Key 走完全流程；评测达 D5 门槛 |
| M2 = Phase 2 | OpenClaw + Codex 包 + 三宿主合约测试 | ~1.5 周 | 同一匿名样本三宿主候选 Schema 兼容、分析一致 |
| M3 = Phase 3 | 间隔复测 + PFA 建模 + 建议效果回顾 | ~2 周 | 能证明"建议→完成→新变式复测→状态更新"链路 |

依赖链：Schema → core 存储/分析 → MCP → 宿主 adapter；评测集从 M0 中期开始积累（M1 出口需要它）。

## 3. M0：协议与可信底座（Phase 0）

目标：不接任何宿主模型，以手工候选 JSON 驱动，跑通"候选 → 确认 → 分析 → 计划 → 复测 → 状态更新"全链路，并让所有可信性约束在 core 层成立。

任务分解（建议顺序）：

1. **仓库脚手架**：按 §17 建目录、`pyproject.toml`、CI（pytest + ruff + jsonschema 校验）、LICENSE、README 骨架。
2. **Schema 三件套**：`candidate-wrong-question-batch` / `analysis-result` / `tool-response`（§10.3、§16 envelope）；校验器 + fuzz 测试（nil、empty、超长、错误类型、NaN/Infinity、枚举越界）。
3. **数据模型与存储**（§13 七类实体）：SQLite + 迁移框架 + schema 版本表；原子写与锁；`good_student_doctor` 的完整性检查基础。
4. **候选暂存区**：`ingest_candidates`（校验、`content_hash` 去重返回已有批次差异、24h 过期）、`list_pending`、`confirm_questions`（支持字段级修改/拒绝）；状态机 §12.1（rejected/expired/failed 不入画像）。
5. **分析引擎 v1**（§14.1 可解释规则）：知识点状态机 §12.2（insufficient → suspected → evidenced → improving → mastered → review_due）；错误集中度计算；无分母不输出掌握率；快照（WeaknessSnapshot）生成。
6. **建议引擎 v1**（§15）：七类错因 × 建议模式 × 复测方式模板；每条建议必含证据、动作、时长题量、复测日期、验收标准、未通过后手。
7. **学生数据治理**：`create_student`（UUID 主键）、`export_student`、`delete_student`（显示范围 + 显式确认）；日志脱敏（不落完整题目/答案/路径）。
8. **MCP server**：§16 全部 11 个工具、统一 envelope、`idempotency_key`、`good_student_capabilities` 健康检查。
9. **共享 Skill 初稿 + 提取 prompt 初稿**（§10.2 边界：不确定返回 null、混合科目拆题、字段级不确定性、附件文本不可信）；模式 B 也依赖它，先写先测。
10. **Core contract tests**：§21.1 清单逐条落测试（含并发写、原子写、迁移失败恢复、重复导入幂等、未确认不进分析、进度不改能力）。

验收对照（§24）：第 4、5、6、7、9、10 条在 core 层通过；CLI 或 MCP 直接调用即可演示完整闭环。

## 4. M1：Hermes 垂直切片 + 数据迁移（Phase 1）

任务分解（进度截至 2026-08-25）：

1. **Hermes 原生插件**（已完成，含真机装配验证 2026-08-25）：`packages/hermes/`（plugin.yaml + `__init__.py`）；`HermesHostBridge` 抽象 + `FakeHermesBridge`；`ctx.llm.complete_structured()` 桥（模式 A，文本/图片 + JSON Schema）；数据目录与配置；18 个 adapter 测试。真机装配：core 以 editable 装进 Hermes venv（`python_dependencies` 只是声明缝，不自动安装），插件目录复制进 `~/.hermes/plugins/` 并 enable；`hermes plugins doctor --ci` 通过（12 工具）；README 5 条 API 假设已逐条标注验证结论。真机缺陷修复：core SQLite 改 thread-local 连接（Hermes 跨线程执行工具处理器）、插件 Service 惰性创建（避免注册副作用建默认库）。
2. **提示词工程**（已定稿）：`prompts/wrong-book-extraction.md` 覆盖 §10.2 全部边界 + 修复重试变体；tests/prompts/ 防漂移测试（内嵌 Schema 与 `schemas/` 解析级全等比对）。针对 §10.2 的对抗样本纳入 evals 场景矩阵，待真实样本扩充。
3. **Skill 定稿**（已完成）：启用判断、同意流程、§19 错误注册表 11 条逐条映射、降级路径与 references 核对一致。
4. **匿名评测集 + 评测脚本**（框架已完成）：`scripts/evaluate_extraction.py`（可插拔 extractor + 五字段准确率/漏题率/误题率/置信度校准指标）；`evals/` 骨架 + 3 个合成占位样本。**遗留：按 `evals/README.md` 用真实匿名材料扩充至 D5 门槛（≥50 题五科），可离线子集进 CI。**
5. **旧数据迁移器**（已完成）：只读迁移 `student-companion-agent` 单文件 JSON；`students[name]` → 稳定 UUID5 Student；score/homework/progress/evidence → `legacy_records` 表（schema v2），整场分数只进科目概览证据；迁移前自动备份、单事务回滚、幂等重跑；`good-student migrate-legacy [--dry-run]`；11 个测试。
6. **端到端验收**（§21.4 单宿主版，已完成 2026-08-25）：真实 Hermes + 真实宿主模型（deepseek/SCNet-Max）跑通三科 20 题合成材料全闭环：extract（20/20 识别，2 处刻意模糊被正确标记）→ list_pending → confirm（20/20，含 edits）→ analyze（29 个疑似薄弱点，无精确掌握率）→ create_plan（18 条动作字段齐全，11 个错因未知诚实 skipped）→ record_reassessment（suspected→improving）+ 2 个负向用例零写入。§24 第 2/3/4/5/6/7/10 条全部通过。证据：`docs/designs/2026-08-25-m1-e2e-evidence.md`，材料：`evals/e2e/m1-e2e-material.txt`。

出口条件：Hermes 上不配置新 API Key 完成全流程（已达）；评测达 D5 门槛（遗留，见任务 4）。

## 5. M2：OpenClaw 与 Codex 包（Phase 2）

任务分解：

1. **发布脚本**：`scripts/build_packages.py` / `validate_packages.py`——共享 Skill、Schema、prompt 单一源复制分发，CI 校验无漂移。
2. **通用 agent-plugin bundle**（`packages/agent-plugin/`：plugin.json + mcp.json + skills），模式 B（Skill 指导宿主模型生成候选 → `good_student_ingest_candidates`）。
3. **Codex 包**（`.codex-plugin/plugin.json` + `.mcp.json` + skills）。
4. **OpenClaw 包**（兼容 bundle，遵循 `PLUGIN_ROOT` / `PLUGIN_DATA`）。
5. **三宿主 contract fixtures**（§21.3）：能力发现、成功识别、无视觉降级、超时/拒绝/非法结构、envelope 一致性、数据目录隔离、Skill 可发现。
6. **附件引用生命周期文档**：每个宿主一节，验证引用失效时的行为（引用失效 ≠ 数据损坏，evidence 降级为摘录）。

出口条件：同一匿名样本在三个宿主生成兼容候选 Schema，核心分析结果一致（§23 Phase 2 / §24 第 8 条）。

## 6. M3：建议闭环优化（Phase 3）

任务分解：

1. **间隔复测调度**：1/3/7/14 天（memory 类错因先行）；`next_review_at` 驱动提醒。
2. **PFA 风格建模**（§14.3）：按知识组件累计成功/失败/复测机会，替换或增强 v1 规则，输出保持可解释（附证据链）。
3. **知识组件包**：课程知识点预置包 + custom 知识点会话式管理（明确标注 custom，不伪装标准知识点）。
4. **建议效果回顾**：北极星指标（疑似薄弱点完成独立复测并产生状态变化的比例）+ §25 辅助指标的本地统计报表。
5. **时间衰减**：mastered 经衰减进入 review_due。

出口条件：能演示"建议 → 完成 → 新变式复测 → 状态更新"完整证据链（§23 Phase 3）。

## 7. 测试与质量贯穿

- 每个里程碑 DoD 包含对应测试绿：M0 = §21.1 全清单；M1 = §21.2 评测；M2 = §21.3 fixtures；M3 = §21.4 端到端。
- 评测集材料需匿名化处理（去除姓名、学校、面部、手写签名）后入库。
- 首版验收清单 §24 共 10 条作为最终 GA 检查表，逐条映射到测试或人工验收步骤。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 宿主模型识别质量不稳定 | 置信度门控 + 人工确认兜底；评测不达 D5 门槛不发布 |
| 逐题确认疲劳导致弃用 | 批量确认界面话术只突出低置信字段；监控每批次确认耗时（§25） |
| 多进程并发写同一数据 | D3 单写者 + 每宿主独立目录 + doctor 检测 + 锁失败不覆盖原文件 |
| 三宿主插件 API 漂移 | contract fixtures 进 CI；锁定验证过的宿主版本 |
| 范围蔓延（App UI、云同步、题库） | §4.2/§26 非目标作为 PR 审查清单 |
| 提示注入与不可信内容 | 评测集含注入样本；字段枚举收紧；Markdown 输出转义 |
| 迁移损坏旧数据 | 只读迁移器 + 自动备份 + 失败即止，绝不回写原目录 |

## 9. 立即可执行的第一步

M0 任务 1–2：建仓 `good-student`（按 §17 结构）+ Schema 三件套与校验器。这两项无任何未决依赖，D1–D5 决策可在 M0 任务 3（存储）开始前确认。
