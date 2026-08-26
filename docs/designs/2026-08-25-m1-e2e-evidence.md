# M1 端到端验收证据（真实 Hermes + 真实宿主模型）

- 日期：2026-08-25
- 宿主：Hermes Agent v0.20.5（git 安装，`~/.hermes/hermes-agent`，Python 3.11.15）
- 默认模型：provider `deepseek`，default_model `SCNet-Max`（用户 config.yaml，未改动）
- 插件：`packages/hermes` 复制至 `~/.hermes/plugins/good-student` 并 `hermes plugins enable`
- 数据隔离：全程 `GOOD_STUDENT_DATA=/tmp/good-student-m1-e2e`（临时目录；`~/.good-student/`
  曾因 doctor 不带环境变量被误建空库，已定位原因、修复并删除，详见「真实 API 差异」第 4 条）
- 材料：`evals/e2e/m1-e2e-material.txt`（合成样本，数学 9 / 语文 6 / 英语 5 共 20 题，
  含 2 处刻意模糊：第 15 题答案未标注学生/标准答案归属；第 4–5 页无科目小标题）
- 会话：`hermes chat -c m1-e2e`（session 20260825_132714_70181a），`-t good-student --yolo`
- 依据：需求文档 §21.4（单宿主版）与 §24 验收条款
- 本文档不含任何 API key；题目内容即合成样本本身

## 装配验证（任务 1）

| 步骤 | 结果 |
| --- | --- |
| `hermes plugins doctor packages/hermes --ci`（修复前） | ERROR: Plugin registration failed: No module named 'good_student' |
| 修复方式 | `~/.hermes/hermes-agent/venv/bin/pip install -e ~/coding/good-student`（editable 装进 Hermes venv） |
| 修复后 doctor（仓库副本） | OK：runtime discovery, manifest parsing, import, registration passed；12 tools, 0 hooks |
| `hermes plugins list` | good-student 0.1.0 出现，Source: user |
| `hermes plugins enable good-student` | 成功；会交互询问是否授予「覆盖内置工具」权限，未授予（本插件不需要） |
| `hermes plugins show good-student` | Status: enabled, Source: user, Key: good-student |
| doctor（已安装副本 `~/.hermes/plugins/good-student`） | OK，12 tools |

### 装配机制结论（源码核对）

- `plugin.yaml` 的 `python_dependencies` 是**纯声明缝**（`hermes_cli/plugins.py:_warn_python_dependencies`）：
  Hermes 只校验并提示 "does not install plugin dependencies automatically; install them yourself"，
  **不会自动安装**。因此 core 必须手工 pip 进 Hermes venv（官方无其他依赖声明机制）。
- 用户插件目录 `~/.hermes/plugins/<name>/` 是目录扫描制（`_collect_directory_manifests`），
  无安装元数据依赖；`hermes plugins install` 只接受 git URL（`file://` 会 clone，
  本仓库 `packages/` 未提交时拿不到内容），本地开发按布局复制目录即可。
- `ctx.llm.complete_structured(instructions=, input=, json_schema=, schema_name=, purpose=,
  temperature=, timeout=)` 签名与返回对象（`.parsed/.text/.provider/.model/.content_type/.audit`）
  与 `agent/plugin_llm.py` 源码一致，真机调用成功。信任门只限制 provider/model 覆盖
  （默认 fail-closed），基础调用（用宿主当前活跃模型）不需要额外授权。

## 端到端步骤（任务 2）

学生：`dbfe7133-2d5a-4a14-a7de-331f304b34f8`（测试学生甲（M1验收），五年级）。
事件流（SQLite `events` 表，与实际调用顺序一致）：
`student_created → candidates_ingested → questions_confirmed → analysis_generated → plan_created → reassessment_recorded`。

### 步骤 1：capabilities + create_student

- `good_student_capabilities` → `ok: true`。`data.tools` 列出全部 12 个工具；
  `storage.data_dir=/tmp/good-student-m1-e2e`（环境变量在 Hermes 进程内生效）；
  `host={name: hermes, vision: true}`；`extraction_mode=A`。
- 首次 `good_student_create_student` → `ok: false`，`persistence_error`：
  "SQLite objects created in a thread can only be used in that same thread"。
  **真机缺陷**：core 单连接线程亲和 × Hermes 多线程工具执行。
  修复：`core/good_student/storage.py` 改为 thread-local 连接（WAL 多连接，
  `check_same_thread=False` 仅供 close() 跨线程清理），新增回归测试
  `tests/core/test_storage_migrations.py::test_cross_thread_access`。111 测试全绿。
- 修复后 `create_student` → `ok: true`，返回 student UUID。

### 步骤 2：extract（宿主模型识别，模式 A，本步骤含插件内 `complete_structured` 调用）

- 调用 `good_student_extract_attachments(student_id, attachments=[{kind:"path",
  ref:"evals/e2e/m1-e2e-material.txt", mime_type:"text/plain"}], source_type="text", page_count=5)`。
- 结果 `ok: true`：**20 题全部识别**入库为候选（`needs_confirmation` 状态），
  `sources` 表 1 行（source_type=text, host=hermes, page_count=5）。
- 科目分布（宿主模型判定）：数学 10 / 语文 6 / 英语 4（第 20 题翻译句被宿主判为数学侧
  邻近项差异，属模型判定自由度，家长确认环节可纠正——本次按原样确认，记录为已知偏差）。
- 刻意模糊命中：第 15 题 `uncertain_fields=["student_answer","correct_answer"]`、
  `extraction_confidence=0.5`；第 18 题 `uncertain_fields=["student_answer"]`、
  置信度 0.9；两题 `attention=true`，其余 18 题 `attention_fields=[]`。
- 识别耗时约 11 分钟（20 题单次结构化输出），未触发修复重试。

### 步骤 3：list_pending + confirm（§24-2 逐题确认）

- `list_pending` → `ok: true`，20 条 pending，`expired_now=0`，
  每题含 candidate_id / source_locator / subject / attention 标记。
- `confirm_questions` 批量 20 条：19 条原样 confirm；第 15 条带家长核对 edits
  `{student_answer: null, correct_answer: "3/2"}`（卷面未标注归属，家长认定学生未作答）。
- 结果 `ok: true`，applied=20，failed=0。确认前 `attempts` 表为 0，确认后写入 20 条
  attempt 并关联知识组件——**未确认数据未进入画像**。

### 步骤 4：analyze（§24-4/5）

- `analyze` → `ok: true`，识别 29 个疑似薄弱点（KCs 由模型候选 + 确认 edits 归并）。
- caveats 原文要点：「无完整作答分母故**不含精确掌握率**；判断均可回溯到已确认题目证据；
  存在少于两条证据的低置信判断」——§24-4/§24-5 直接命中。
- 快照示例：`异分母分数加法` status=`suspected_weakness`，evidence_count=2，
  confidence=low；`一元一次方程` suspected，evidence=1。
- 确认后候选状态 needs_confirmation → analyzed（DB 核查：20/20）。

### 步骤 5：create_plan（§24-6）

- `create_plan` → `ok: true`，**18 条学习动作**全部含
  `action / duration_minutes / question_count / due_date / acceptance_criteria /
  reassessment_method / next_step_if_fail / why / error_reason`。
- 示例：异分母分数加法 25 分钟/3 题/due 2026-08-28/验收「新变式无提示正确率≥80%」；
  多音字 10 分钟/5 题/due 2026-08-26/「1/3/7/14 天间隔回忆≥80%」（按错因定制）。
- **skipped=11**：错因为 unknown 的知识点不生成动作（诚实降级，提示需先确认错因），
  不编造建议。

### 步骤 6：record_reassessment（§24-7）

- 对 `异分母分数加法`（kc ae1aded8-…）记录复测：3 道新变式、无提示、全对、420 秒。
- 结果 `ok: true`，passed=true，accuracy=1.0；`updated_weakness`：
  status `suspected_weakness → improving`（改善中），evidence_count 2→3，
  confidence 仍为 low（单次通过推进状态但不夸大置信），reason_codes 增加
  `passed_independent_reassessment`。
- 对照：计划动作创建后、复测前，快照状态维持 `suspected_weakness` 不变——
  **计划完成本身不伪造掌握提升**。

### 步骤 7：负向验证（§24-10）

- `extract_attachments` 指向不存在路径 → `ok: false`，`error.code=no_attachment`，
  未调用宿主模型、未写入（DB 核查 sources 仍为 1）。
- `ingest_candidates` 传入非法 batch（缺 source_ref、questions 空数组）→
  `ok: false`，`schema_validation_failed`，未写入（candidates 仍为 20）。
- 宿主模型侧两次识别输出均一次性通过 Schema 校验，未触发 §19 修复重试路径。

## §24 验收核对（文本模式可验证条款）

| 条款 | 结论 | 证据 |
| --- | --- | --- |
| 2. 识别结果写入前逐题确认 | 通过 | 候选入库即为 needs_confirmation；confirm 前 attempts=0；confirm 带 edits 生效（第 15 题） |
| 3. 低置信字段和失败页清楚可见 | 通过 | 第 15/18 题 uncertain_fields + extraction_confidence + attention=true 在 list_pending 可见 |
| 4. 无正确作答分母时不显示精确掌握率 | 通过 | analyze caveats 明确「不含精确掌握率」 |
| 5. 每个薄弱点可回溯至少一条已确认题目证据 | 通过 | analyze caveats + 快照 evidence_count ≥1，证据即已确认 attempt |
| 6. 每条建议含动作、时长、题量、复测日期、验收标准 | 通过 | 18 条 action 字段逐一齐全；错因未知的 11 个知识点诚实 skipped 不编造 |
| 7. 复测改变状态，计划完成不伪造掌握提升 | 通过 | 复测后 suspected→improving、evidence 2→3；计划创建前后快照不变 |
| 10. 宿主模型失败/拒绝/非法结构时不写入正式数据 | 通过 | 附件不可读/Schema 非法两个负向用例均零写入（DB 行数核查） |

补充：条款 1（三科混合识别）在文本模式下亦已覆盖——单次 extract 产出数学/语文/英语三科候选。

## 发现的真实 API 差异与修复

1. **`python_dependencies` 不自动安装**：纯声明缝，Hermes 只告警提示手工 pip install
   （`hermes_cli/plugins.py:4790`）。装配修复 = editable 安装 core 进 Hermes venv。
2. **工具处理器跨线程执行**（文档未写明）：导致 core SQLite 单连接报
   `persistence_error`。已修 `core/good_student/storage.py` 为 thread-local 连接 + 回归测试。
3. **`hermes chat -t good-student` 报 "Unknown toolsets" 警告**：CLI 的 toolset 校验
   （`cli.py:5318`）只认静态表，插件 toolset 在注册表后期才可见；工具实际可正常调用，
   警告为表面现象，建议上游修或文档注明。
4. **register() 副作用**：插件在注册时（每次 Hermes 启动/doctor）即建默认数据目录空库，
   曾误建 `~/.good-student/`（空库，已删除）。已修 `packages/hermes/plugin.py` 为
   惰性创建 Service（首次工具调用才落地数据目录），doctor 不再产生副作用。
5. 其余假设（`register_tool`/`register_skill`/`get_config`/`complete_structured` 签名、
   envelope 字符串返回）全部与真机一致，详见 `packages/hermes/README.md`「API 假设」逐条标注。

## 模型调用预算

宿主会话共 9 个用户消息轮次（含 1 次修复后重跑 create_student），插件内
`complete_structured` 识别调用 1 次（一次通过，无修复重试）。合计约 10 次模型调用，
在 ≤15 预算内。

## 遗留问题

- 图片/PDF 路径（视觉识别、`--image` 附件传入）未在本次验收范围，假设 1/2 仅文本路径验证。
- 识别 20 题单批耗时 ~11 分钟；大批量材料可能需要分页/分批策略（产品层决策）。
- "Unknown toolsets" 警告与 skipped=11 的错因补全流程建议在 M2 处理。
- 验收结束后插件保留安装、已 disable；临时数据目录 `/tmp/good-student-m1-e2e` 保留供复查。
