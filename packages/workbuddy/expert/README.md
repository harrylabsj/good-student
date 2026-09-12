# WorkBuddy 专家包（Expert）

`good-student` 专家「错题教练」，与同名 MCP 连接器配套使用。

## 结构

```text
expert/
├── .codebuddy-plugin/
│   └── plugin.json          # 专家配置与市场展示信息
├── avatars/
│   └── expert.png           # 512×512 头像（<500KB）
└── agents/
    └── wrong-book-coach.md  # 专家系统提示词
```

## 要点

- `plugin.json` 通过 `dependencies.connectors: ["good-student"]` 声明依赖，用户召唤专家时
  WorkBuddy 会引导先连接 `good-student` 连接器。
- 专家提示词与连接器内嵌 Skill 同源：工作流与安全边界以仓库根目录
  `skills/good-student/SKILL.md` 为唯一源，修改后同步重写 `agents/wrong-book-coach.md`，
  禁止两边手工漂移。
- 平台硬性约束（构建脚本会校验）：中文展示描述 40–50 字；tags 与 quickPrompts 各 3 条；
  `defaultInitPrompt` 与第一条 quickPrompt 一致；头像 512×512 PNG 且小于 500KB。

## 提交顺序

先提交并上架 `good-student` 连接器，再提交本专家包，否则依赖引导无法完成连接。
