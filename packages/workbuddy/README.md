# WorkBuddy 交付包

Good-student 对 WorkBuddy 采用「专家 + 连接器」结构：

1. 先提交 `connector/`（CLI + Skill 连接器）。`cli.json` 声明平台托管 Python 运行时
   （5.0.0+），安装命令直接 `pip install` 连接器包内置的 wheel（`pkg/*.whl`，打包时从
   仓库构建注入）——**不需要发布 PyPI、不需要 uvx、不需要用户预装 Python**。数据默认
   保存在用户本机 `~/.good-student/`。
2. 连接器上架后提交 `expert/`「错题教练」专家，其 `plugin.json` 通过 `dependencies.connectors` 声明依赖本连接器，召唤时平台自动引导连接。
3. `skill/` 独立 Skill 可同步提交（可复用资产，非首发主路径）。
4. Buddy 应用（`buddy-app-profile.json`）为可选的第三阶段；不要申请不必要的 OAuth 权限。

## 提交前置条件

- 运行 `scripts/build_workbuddy_packages.py`（需 `pip install build`），生成三个上传 ZIP
  并通过平台约束校验。
- 待平台联调确认：`cli.json` 的 `init` 使用相对路径 `./pkg/<wheel>`，依赖平台以连接器
  解压目录为工作目录执行 init；若不支持，备选是把 wheel 发到包源后按名安装（见
  `docs/workbuddy-publishing-plan.md`）。
- `connector-meta.json`、`cli.json`、Skill 和示例中不放真实密钥；未成年人数据默认 local-first。

平台要求和发布步骤记录在 `docs/workbuddy-publishing-plan.md`；逐步可勾选的上线清单见
`docs/workbuddy-release-checklist.md`。
