# WorkBuddy 交付包

Good-student 对 WorkBuddy 首发采用独立专家包：

1. 提交 `expert/`「学生错题本」专家。构建产物内嵌 Skill、`.mcp.json`、启动脚本 和
   `pkg/*.whl`；包内脚本创建隔离 Python 环境，安装本地 wheel 后启动 stdio MCP。
   **不需要发布 PyPI、不需要 uvx、需要本机 Python 3.10+，首次连接需要联网安装 MCP 依赖**。数据默认保存在本机
   `~/.good-student/`。
2. `connector/` 保留为兼容和独立复用方案，不是专家运行依赖。
3. `skill/` 可作为独立复用资产同步提交。
4. Buddy 应用（`buddy-app-profile.json`）为可选后续阶段。

## 提交前置条件

- 运行 `scripts/build_workbuddy_packages.py`（需 `pip install build`），生成三个上传 ZIP
  并通过平台约束校验。
- 使用生成的 `.mcp.json` 验证首次启动与缓存重连；真实 WorkBuddy 客户端和平台机审仍需验证。
- `connector-meta.json`、`cli.json`、Skill 和示例中不放真实密钥；未成年人数据默认 local-first。

平台要求和发布步骤记录在 `docs/workbuddy-publishing-plan.md`；逐步可勾选的上线清单见
`docs/workbuddy-release-checklist.md`。
