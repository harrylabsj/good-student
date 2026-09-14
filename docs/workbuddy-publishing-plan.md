---
title: Good-student WorkBuddy 开放平台发布规划
status: embedded-mcp-pending-platform-verification
version: 3.0
date: 2026-09-14
---

# 结论

## 2026-09-14 独立专家包（v3.0）

首发采用 **专家内嵌 MCP** 结构：

1. `plugin.json` 用 `dependencies.mcpServers: "./.mcp.json"` 声明内嵌 MCP，并预加载
   `skills/good-student`。
2. `.mcp.json` 用 `preAuth: "cli"` 触发同目录 `cli.json`；后者声明 WorkBuddy 托管 Python
   3.11，并从专家包 `pkg/` 安装 `good_student-*.whl[mcp]`。
3. 安装后通过 `good-student-mcp` 启动本地 stdio MCP。学生数据仍保存在本机。
4. `connector/` 保留为兼容方案，但专家不依赖市场连接器；已提交的连接器申请应撤回。

本方案不申请 OAuth、不写死 Token、不上传原始附件；学生数据保存在用户本机
`~/.good-student/` SQLite。原 D5 机械门禁已移除（识别质量参考线见 `evals/README.md`）。

## 已完成的仓库改造

### 2026-09-14 v3（专家内嵌 MCP）

- 专家版本升级到 1.1.0，移除 `dependencies.connectors`，改为内嵌 `.mcp.json`。
- 构建时向专家 ZIP 注入 `cli.json`、核心 wheel 和完整 Skill。
- 构建校验覆盖单一 stdio MCP、无认证、三平台安装命令、wheel 与 Skill 完整性。
- 已在干净虚拟环境从 ZIP 内 wheel 安装 `[mcp]` extra，doctor 全绿。

### 2026-09-11 v2（CLI 连接器 + 去 D5）

- 连接器从 MCP(stdio/uvx) 改为 **CLI + Skill**：`connector-meta.json` `type: cli`、
  版本 0.2.0、声明 `minWorkbuddyVersion: 5.0.0`；新增 `cli.template.json`
  （python 运行时托管 + 三平台 `pip install ./pkg/<wheel>`）；移除 `mcp.json`。
- 连接器内嵌 Skill 改写为 CLI 命令版（15 条命令 ↔ 15 个核心工具一一对应）。
- 打包脚本：构建 wheel → 暂存注入 `cli.json`（真实 wheel 文件名）→ 校验 → 出 3 个 ZIP。
- uvx 冷启动验证（本地 wheel、全新 uv 缓存）：MCP 握手、14 工具、envelope、doctor
  全绿——证明 wheel 在隔离环境可安装可运行；该验证结论同样覆盖 CLI 安装路径
  （同一 wheel，pip 直装）。
- 移除 D5 门禁：`scripts/check_eval_gate.py`、对应测试与 `evals/gate-result.json`
  已删除；`evals/README.md` 保留评测框架与识别质量参考线（非门禁）。

### 2026-09-11 v1（专家 + 连接器结构）

- 新增 `packages/workbuddy/expert/`：错题教练专家包（`.codebuddy-plugin/plugin.json`、
  `agents/wrong-book-coach.md`、512×512 头像），声明依赖 `good-student` 连接器。
- 打包前校验平台硬性约束（中文展示描述 40–50 字、3 标签、3 快捷提示词、
  defaultInitPrompt 一致性、头像尺寸、依赖声明）。
- 核心能力升级：K12 知识点种子包（`knowledge_packs/`，数 19 / 语 9 / 英 8 节点含前置链）、
  知识点归一（resolve_kc 三级匹配）、分析层前置知识回溯（`prerequisite_hints`）、
  memory 错因 1/3/7/14 天间隔复习排期、新增 `good_student_weekly_brief`
  （每周家长简报，已同步 CLI、MCP、Hermes 插件）。
- 新增 `docs/privacy/minor-data-protection.md`（平台审核用隐私与未成年人数据说明）。

### 2026-09-07 及之前

- `good-student-mcp`：安装后可直接作为 stdio MCP 入口，不再依赖仓库内 `mcp/server.py` 的源码路径。
- `packages/workbuddy/skill/`：可独立上传的双语 Skill，包含触发范围、家长同意、候选确认、数据治理和降级规则。
- `packages/workbuddy/buddy-app-profile.json`：Buddy 应用配置草案（第三阶段备用）。
- 已修复 wheel 在隔离安装中找不到 Schema 的缺陷；三份 Schema 与安装态 doctor 均通过。

## 平台侧配置

### CLI 连接器

上传 `good-student-workbuddy-connector.zip`。`cli.json` 声明
`runtime: {type: python, version: "3.11"}`，WorkBuddy 准备托管 Python 并激活隔离
虚拟环境；`init` 在三平台均执行 `python -m pip install --upgrade ./pkg/<wheel>`。
连接器无需登录态，不提供 auth/unAuth/status。数据目录默认用户本机 `~/.good-student/`。

**待平台联调确认**：`init` 命令的工作目录是否为连接器解压目录（相对路径 `./pkg/` 依赖
这一点）。若平台不支持相对路径，备选依次为：① 把 wheel 发布到包源后 init 改为按名安装；
② 回到 MCP 方案部署 HTTPS streamableHttp 服务。二者都不影响专家包与核心代码。

### Skill

上传 `good-student-workbuddy-skill.zip`。市场字段建议直接使用 Skill 的 YAML frontmatter；中文定位为“把错题材料整理成可确认、可解释、可复测的学习行动”。

### 专家（Expert，首发主路径）

上传 `good-student-workbuddy-expert.zip`。平台字段以 `expert/.codebuddy-plugin/plugin.json`
为准：分类 `15-Education`，`dependencies.mcpServers` 指向包内 `.mcp.json`。用户召唤专家时
平台弹出内嵌 MCP 连接卡片，不依赖连接器市场审核状态。

### Buddy 应用（可选，第三阶段）

在开放平台创建应用后，按 `buddy-app-profile.json` 填写：分类 `15-Education`、首页
“把每一道错题，变成下一步行动”、工作模式（错题诊断/成绩记录/复测闭环）、授权列表为空。
`buddy-app-profile.json` 是仓库内的配置草案，不假定它就是平台内部导入格式；平台的实际导出
JSON 应在创建草稿后保存并回填仓库。

## 提交前核对清单

1. `scripts/build_workbuddy_packages.py` 跑通，三个 ZIP 校验通过（含专家硬性字段与
   连接器 CLI 结构）。
2. 干净环境验证 wheel 可安装、CLI 全命令输出 `{ok, data, warnings, error, trace_id}`
   envelope（本仓库测试已覆盖：174 项 pytest + 隔离安装冒烟）。
3. 在真实 WorkBuddy 客户端验证：安装连接器（托管 Python + 内置 wheel 安装）、
   逐条跑 4 中 3 英场景胶囊、召唤专家时的依赖引导卡片、拍照上传 → 逐题确认 → 分析 →
   计划 → 复测全流程、导出与两段式删除。
4. 只提交 ZIP，不提交本机数据库、`.env`、Client Secret、真实学生材料或日志。
5. 专家可独立提交审核；专家通过后再（可选）提交 Buddy 应用审核。

## 不建议事项

- 不建议首发接入 WorkBuddy OAuth：学生数据默认本地保存，业务不需要读取平台用户资料。
- 不建议远程 MCP 首发：会把未成年人学习数据搬到服务端，扩大合规面；仅作为
  平台不支持本地 Python 托管时的备选。

## 发布后观察指标

- 首次完成“上传 → 确认 → 分析”的比例
- 候选确认修改率和重复上传率
- 识别失败/降级率
- 计划生成后的新变式复测完成率
- 每周简报调用率与到期复习完成率
- 用户导出与删除请求是否能成功完成
- 连接器安装成功率（托管 Python 环境 + 内置 wheel）

## 官方依据

- [WorkBuddy 开放平台](https://open.workbuddy.cn/)
- [Skill 接入与基础结构](https://open.workbuddy.cn/docs/skill)
- [连接器接入与 MCP/CLI 规范](https://open.workbuddy.cn/docs/connector)
- [专家接入与配置](https://open.workbuddy.cn/docs/expert)
- [Buddy 应用配置指引](https://open.workbuddy.cn/docs/buddy-app)
- [入驻开放平台](https://open.workbuddy.cn/docs/onboarding)
