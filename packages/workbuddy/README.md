# WorkBuddy 交付包

Good-student 对 WorkBuddy 首发采用独立专家包：

1. 提交 `expert/`「学生错题本」专家。构建产物内嵌 Skill、`.mcp.json`、`cli.json` 和
   `pkg/*.whl`；WorkBuddy 托管 Python 环境安装本地 wheel 后启动 stdio MCP。
   **不需要发布 PyPI、不需要 uvx、不需要用户预装 Python**。数据默认保存在本机
   `~/.good-student/`。
2. `connector/` 保留为兼容和独立复用方案，不是专家运行依赖。
3. `skill/` 可作为独立复用资产同步提交。
4. Buddy 应用（`buddy-app-profile.json`）为可选后续阶段。

## 提交前置条件

- 运行 `scripts/build_workbuddy_packages.py`（需 `pip install build`），生成三个上传 ZIP
  并通过平台约束校验。
- 待平台联调确认：专家内嵌 MCP 的 `preAuth: "cli"` 是否在同一托管 Python 环境中完成
  wheel 安装并让 `good-student-mcp` 进入 PATH；若不支持，备选是独立连接器方案（见
  `docs/workbuddy-publishing-plan.md`）。
- `connector-meta.json`、`cli.json`、Skill 和示例中不放真实密钥；未成年人数据默认 local-first。

平台要求和发布步骤记录在 `docs/workbuddy-publishing-plan.md`；逐步可勾选的上线清单见
`docs/workbuddy-release-checklist.md`。
