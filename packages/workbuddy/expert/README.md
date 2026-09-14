# WorkBuddy 专家包（Expert）

`good-student` 专家「学生错题本」，内嵌 Skill、本地 stdio MCP 和核心 wheel。
用户召唤后由 WorkBuddy 托管 Python 环境安装包内 wheel，无需单独安装市场连接器，
也无需发布 Good-student 到 PyPI。

## 结构

```text
expert/
├── .codebuddy-plugin/
│   └── plugin.json          # 专家配置与市场展示信息
├── .mcp.json                # 内嵌 stdio MCP 与连接卡片
├── cli.json                 # 托管 Python 环境安装包内 wheel
├── avatars/
│   └── expert.png           # 512×512 头像（<500KB）
├── agents/
│   └── wrong-book-coach.md  # 专家系统提示词
├── skills/good-student/     # MCP 使用流程与安全边界
└── pkg/*.whl                # Good-student 核心与 MCP 入口
```

## 要点

- `plugin.json` 通过 `dependencies.mcpServers: "./.mcp.json"` 声明内嵌 MCP；不依赖市场连接器。
- `.mcp.json` 使用 `preAuth: "cli"`，连接前由 `cli.json` 在 WorkBuddy 托管 Python 环境中
  安装 `pkg/*.whl[mcp]`，随后通过 `good-student-mcp` 启动 stdio MCP。
- 专家提示词与连接器内嵌 Skill 同源：工作流与安全边界以仓库根目录
  `skills/good-student/SKILL.md` 为唯一源，修改后同步重写 `agents/wrong-book-coach.md`，
  禁止两边手工漂移。
- 平台硬性约束（构建脚本会校验）：中文展示描述 40–50 字；tags 与 quickPrompts 各 3 条；
  `defaultInitPrompt` 与第一条 quickPrompt 一致；头像 512×512 PNG 且小于 500KB。

## 提交

专家包可以独立提交。首次召唤时会显示内嵌 MCP 连接卡片；无需预先上架市场连接器。
