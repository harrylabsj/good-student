# Good-student × WorkBuddy 上线检查清单

- 版本：v1.1（2026-09-14）
- 结构：独立专家（内嵌 Skill + stdio MCP + wheel）→（可选）Buddy 应用
- 原则：不发布 PyPI、不用 uvx、不申请 OAuth、学生数据只在本机

---

## 第 0 步：打包（本地，每次提审前必跑）

- [ ] `cd ~/coding/good-student && .venv/bin/pytest -q` 全绿（当前 160 项）
- [ ] `.venv/bin/ruff check core/good_student scripts tests` 无报错
- [ ] `.venv/bin/python scripts/build_workbuddy_packages.py` 产出三个 ZIP 且校验通过
- [ ] 三个 ZIP 内无密钥、无本机数据库、无真实学生材料（打包脚本已做 token/secret 扫描，
      人工再抽查一遍 `unzip -l`）

产物：

| 包 | 路径 | 提交到 |
| --- | --- | --- |
| 连接器 | `packages/workbuddy/dist/good-student-workbuddy-connector.zip` | 开放平台 → 连接器 |
| 专家 | `packages/workbuddy/dist/good-student-workbuddy-expert.zip` | 开放平台 → 专家（主路径） |
| Skill | `packages/workbuddy/dist/good-student-workbuddy-skill.zip` | 开放平台 → 技能（可同步，非主路径） |

## 第 1 步：开发者入驻（一次性）

- [ ] 完成企业认证或管理员实名认证（身份证 + 实名手机号 + 邮箱 + 人脸核身）
- [ ] 准备好开发者昵称（对外展示，需过命名规范校验）

## 第 2 步：专家内嵌 MCP 安装验证（关键风险点）

- [ ] 上传专家 ZIP，观察是否解析失败（失败则按 `.mcp.json` / `cli.json` 排错，或邮件
      openworkbuddy@tencent.com）
- [ ] **在真实 WorkBuddy 客户端召唤专家并连接内嵌 MCP**，重点验证：`preAuth: "cli"`
      能执行 `python -m pip install --upgrade './pkg/good_student-0.1.1-py3-none-any.whl[mcp]'`
      是否以专家包解压目录为工作目录执行
  - ✅ 成功 → 继续
  - ❌ 失败（找不到 ./pkg 路径）→ 启用备选：把 wheel 发到包源，init 改为
    `python -m pip install --upgrade good-student==0.1.0`，重打包重提交
- [ ] 安装完成后在对话里让 AI 调用 `good-student capabilities`：返回 15 个工具、
      envelope 完整
- [ ] 低版本客户端（<5.0.0）表现符合预期：未启用过的不展示，已启用的置灰提示升级
- [ ] 跑一遍 `good-student doctor`：全绿（含 knowledge_packs: 3 packs loaded）

## 第 3 步：连接器端到端场景（真实客户端）

- [ ] 中文胶囊 4 条逐条跑通（拍照整理错题 / 查看薄弱点 / 本周计划 / 记录复测 / 周报）
- [ ] 英文胶囊 3 条逐条跑通
- [ ] 拍照全流程：上传图片 → 候选只进临时区 → 逐题确认 → analyze → plan → reassess
- [ ] 异常路径抽查：重复上传不重复入库；未确认候选不能分析；删除必须两段式
      （先范围预览，再显示名确认）
- [ ] 知识点归一抽查：上传含"分数解决问题"的错题，确认入库后知识点显示为
      标准节点"分数应用题"（is_custom=false），分析带 prerequisite_hints
- [ ] 数据落点确认：`~/.good-student/` 下有 SQLite 文件，无数据外流

## 第 4 步：专家提交

- [ ] 上传专家 ZIP，确认平台字段解析：分类 15-Education、中文描述 40–50 字、
      3 标签、3 快捷提示词、头像显示正常
- [ ] **实测内嵌 MCP 引导**：未连接状态下召唤「学生错题本」→ 弹出包内 MCP
      引导卡片 → 完成连接 → 进入对话
- [ ] 已连接状态下再次召唤，不重复引导
- [ ] 专家首条消息符合人设：先问哪位学生、说明家长同意与候选确认机制
- [ ] 用 defaultInitPrompt（=第一条 quickPrompt）跑完整拍照流程

## 第 5 步：合规材料

- [ ] 随审核附上 `docs/privacy/minor-data-protection.md` 要点：本机存储、最小化收集、
      可导出、两段式删除、无 OAuth、无模型凭据
- [ ] 市场文案自查：无"诊断""提分保证""能力评估"类表述
- [ ] 确认专家/连接器所有示例不涉及真实学生姓名与学校

## 第 6 步：发布后观察（首周每天看）

- [ ] 连接器安装成功率（托管 Python + 内置 wheel 安装失败率）
- [ ] 首次完成「上传 → 确认 → 分析」的比例
- [ ] 候选确认修改率、重复上传率、识别降级率
- [ ] 新变式复测完成率、weekly-brief 调用率、到期复习完成率
- [ ] 导出/删除请求成功率
- [ ] 紧急停用开关：如需下架，`connector-meta.json` 的 `maxWorkbuddyVersion` 可限制
      高版本拉取；平台侧下架以运营沟通为准

## 回滚预案

- 连接器出新版：版本号 +1，重新打包提交，审核通过后 10–15 分钟同步生效
- 专家文案/提示词修订：仅改 `expert/` 包，不影响连接器与本地数据
- 数据兼容：SQLite 迁移由 core 的 migrations 自动处理；发布新版 wheel 前跑
  `pytest tests/core/test_storage_migrations.py`

---

### 当前状态速查（2026-09-11）

| 项 | 状态 |
| --- | --- |
| 三个 ZIP 构建与平台约束校验 | ✅ 已通过（脚本自动校验） |
| wheel 隔离安装 + 全流程冒烟（干净 venv，相对路径 `./pkg/`） | ✅ 本机通过 |
| 真实 WorkBuddy 客户端安装验证（init 相对路径） | ⬜ 待做——**全清单唯一技术未知项** |
| 开发者入驻认证 | ⬜ 待做 |
| 专家内嵌 MCP 引导实测 | ⬜ 待做（专家 1.1.0 上传后） |
| D5 机械门禁 | 已移除（识别质量参考线见 `evals/README.md`） |
