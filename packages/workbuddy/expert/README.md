# WorkBuddy 专家包（Expert）

`good-student` 专家「学生错题本」，内嵌 Skill、本地 stdio MCP 和核心 wheel。
本机需提供 Python 3.10+（`python3` 命令）。Linux x86_64（glibc 2.28+）的
CPython 3.11/3.12 和打包机器对应的 Python 环境可离线完成首次连接。
若系统首先找到的是其他 Python，启动脚本会优先选用与包内依赖匹配的已安装解释器。
包内启动脚本直接加载随包提供的依赖，无需单独安装市场连接器，
也无需发布 Good-student 到 PyPI。

## 结构

```text
expert/
├── .codebuddy-plugin/
│   └── plugin.json          # 专家配置与市场展示信息
├── .mcp.json                # 内嵌 stdio MCP 与连接卡片
├── bin/start_mcp.py         # 隔离环境首次安装及缓存启动
├── runtime-constraints.txt  # 已验证的直接依赖版本
├── offline-targets.json     # 包内有完整依赖的运行环境
├── wheelhouse/              # 第三方运行依赖
├── avatars/
│   └── expert.png           # 512×512 头像（<500KB）
├── agents/
│   └── wrong-book-coach.md  # 专家系统提示词
├── skills/good-student/     # MCP 使用流程与安全边界
└── pkg/*.whl                # Good-student 核心与 MCP 入口
```

## 要点

- 专家通过根目录的 `.mcp.json` 声明内嵌 MCP；不依赖市场连接器。
- `.mcp.json` 通过 `./bin/start_mcp.py` 启动；安装日志仅写 stderr。
  环境缓存在 `~/.cache/good-student-mcp`（可用 `GOOD_STUDENT_RUNTIME` 覆盖），安装失败允许重试，后续连接复用缓存。
- 专家 Skill 使用 MCP 工具名，连接器 Skill 使用 CLI 命令；共享参考文档，避免两种调用约定混用。
- 离线覆盖的环境直接解包加载依赖，无需 pip 或虚拟环境。其他环境首次连接仍需联网。
- macOS 冷启动与缓存重连由发布测试覆盖；Windows 的 `python3` 命令和平台沙箱环境仍需实际验证。
- 平台硬性约束（构建脚本会校验）：中文展示描述 40–50 字；tags 与 quickPrompts 各 3 条；
  `defaultInitPrompt` 与第一条 quickPrompt 一致；头像 512×512 PNG 且小于 500KB。

## 提交

专家包可以独立提交。首次召唤时会显示内嵌 MCP 连接卡片；无需预先上架市场连接器。
