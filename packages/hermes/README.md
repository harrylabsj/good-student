# good-student Hermes 插件

Hermes 原生插件（`plugin.yaml` + `register(ctx)`，设计文档 §9/§17）。宿主模型负责
把错题附件识别为结构化候选（模式 A，`ctx.llm.complete_structured()`），确定性 core
（`good_student.service`）负责校验、存储、分析与建议。候选必须经家长确认后才进入
学习画像；任何识别失败都不写入数据（§19）。

## 结构

```text
packages/hermes/
├── plugin.yaml        # Hermes 插件清单（manifest v2）
├── __init__.py        # register(ctx)：注册 12 个工具 + namespaced Skill
├── bridge.py          # HermesHostBridge 协议 + HermesBridge（包装 ctx）
├── plugin.py          # GoodStudentPlugin：识别编排、§19 降级、envelope
├── tool_schemas.py    # 工具 Schema（模型可见的描述与参数）
├── testing.py         # FakeHermesBridge（内存实现，供测试与其他宿主参考）
└── skills/good-student/
    ├── SKILL.md       # 由发布脚本从仓库根 skills/good-student/ 同步，勿直接编辑
    └── references/    # 同上
```

## 安装（M1 真机验证 2026-08-25，Hermes Agent v0.20.5）

```bash
# 1. 安装 core 到 Hermes 的 Python 环境（关键：Hermes 不会自动安装 python_dependencies，
#    该 manifest 字段只是声明性校验缝——缺失时只告警提示 "pip install ..."）
~/.hermes/hermes-agent/venv/bin/pip install -e ~/coding/good-student   # 或发布后的 good-student 包

# 2. 安装插件：Hermes 的用户插件目录就是 ~/.hermes/plugins/<name>/（目录+manifest 扫描，
#    无额外注册表）；`hermes plugins install` 只接受 git URL，本地开发直接复制目录即可
cp -r ~/coding/good-student/packages/hermes ~/.hermes/plugins/good-student
hermes plugins enable good-student   # 会询问是否允许覆盖内置工具，选不授权（本插件不需要）

# 3. 自检（0 ERROR，12 个工具注册）
hermes plugins doctor good-student --ci        # 已安装副本
hermes plugins doctor packages/hermes --ci     # 仓库内开发副本
```

注意：editable 安装让 `good_student.__file__` 指向仓库，`prompts/`、`schemas/` 经仓库根
回退路径解析（见 `plugin.py:_find_repo_root`）；发布脚本会把这些资产同步进插件目录。
非 editable 安装时必须使用发布脚本同步后的自包含插件目录。

启用后在会话中调用 `good_student_capabilities` 验证；加载工作流 Skill：
`skill_view("good-student:good-student")`。

## 配置（`config.yaml` → `plugins.entries.good-student.settings`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `data_dir` | `""` | 学生数据目录；缺省 `~/.good-student/`（或 `GOOD_STUDENT_DATA` 环境变量） |
| `vision_capable` | `true` | 宿主无视觉能力时显式置 `false`，图片材料走文本降级路径 |

插件不持有任何模型凭据：识别调用使用宿主当前活跃的 provider/model
（Hermes 信任门默认 fail-closed，本插件不请求 provider/model 覆盖能力）。

## Hermes API 假设

本插件依据以下 Hermes 官方文档实现（2026-08 抓取成功）：

- [Plugin LLM Access](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugin-llm-access.md)：
  `ctx.llm.complete_structured(instructions=, input=, json_schema=, schema_name=, purpose=, temperature=, timeout=)`，
  返回对象带 `.parsed`（dict 或 None）、`.text`、`.provider`、`.model`、`.content_type`；
  Schema 校验失败或空输入抛 `ValueError`；超时/供应商错误抛底层异常。
- [Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)：
  `register(ctx)` 入口；`ctx.register_tool(name=, toolset=, schema=, handler=)`，
  处理器签名为 `(args: dict, **kwargs) -> str`（JSON 字符串）；`ctx.register_skill(name, path)`；
  `ctx.get_config(key, default=)` 读取 `plugins.entries.<id>.settings`；
  未知 manifest 字段被忽略（前向兼容）。

仍需在真实 Hermes 运行时验证的假设（2026-08-25 M1 真机验收，Hermes Agent v0.20.5）：

1. **视觉能力探测**——部分验证：`ctx.llm` 确实没有公开的视觉能力查询 API（已核对
   `agent/plugin_llm.py` 源码），`HermesBridge.capabilities()` 维持默认 `vision=True` +
   `vision_capable: false` 显式降级。图片路径本次未验证（文本模式验收）。
2. **附件获取**——已验证（文本路径）：`kind="path"` 的本地文本附件经 `resolve_attachments`
   读入为 text block，进入 `complete_structured` 正常。图片附件与 Hermes 会话层
   `--image` 传入路径本次未验证。
3. **完整 JSON Schema 透传**——已验证：`ctx.llm.complete_structured(instructions=,
   input=, json_schema=, schema_name=, purpose=, temperature=, timeout=)` 签名与返回对象
   （`.parsed`/`.text`/`.provider`/`.model`/`.content_type`/`.audit`）与
   `agent/plugin_llm.py` 源码一致；含 `$defs`/`const`/嵌套引用的候选批次 Schema
   被接受（本地 jsonschema 校验），当前 provider（deepseek/SCNet-Max）未拒绝复杂 Schema。
4. **工具返回**——已验证：处理器返回 envelope JSON 字符串，模型侧可见并可解析；
   `ctx.register_tool(name=, toolset=, schema=, handler=)` 与 `ctx.register_skill(name, path)`
   均被 doctor 确认注册成功（14 工具 + 1 Skill）。注意：`hermes chat -t good-student`
   会打印 "Unknown toolsets: good-student" 警告（CLI 的 toolset 校验只认静态表，
   插件 toolset 在注册表后期才可见），但工具实际可调用，警告为表面现象。
5. **数据目录**——已验证：未使用 `plugin_storage`；`GOOD_STUDENT_DATA` 环境变量在
   Hermes 子进程内对 core 生效（capabilities 报告 data_dir 为环境变量指向目录），
   `data_dir` 配置项经 `ctx.get_config("data_dir")` 读取的兜底路径可用。

另发现并已修复的真实缺陷：core 的 SQLite 单连接有线程亲和限制，Hermes 在与插件
注册线程不同的线程执行工具处理器，写操作报 `persistence_error`（thread id 不匹配）。
已把 `core/good_student/storage.py` 改为 thread-local 连接（WAL 多连接），
附回归测试 `test_cross_thread_access`。

## 错误与降级（§19 映射）

| 场景 | envelope error.code | 行为 |
| --- | --- | --- |
| 无附件 / 附件不可读 | `no_attachment` | 不调用模型 |
| 宿主无视觉能力（图片材料） | `host_capability_missing` | 不调用模型，提示文本/手工录入 |
| 宿主模型超时/拒绝/限流 | `host_llm_error` | 产品级重试一次；仍失败不写入 |
| 非法 JSON / Schema 不匹配 | `structured_extraction_failed` | 修复提示重试一次；仍失败不写入、不展示伪结果 |
| 提取指令/Schema 资产缺失 | `asset_missing` | 不调用模型 |
| core 校验失败 | `schema_validation_failed`（core 返回） | 不写入 |

## 测试

Hermes 运行时不可用于本仓库测试，宿主桥经 `HermesHostBridge` 协议隔离，
`testing.FakeHermesBridge` 提供内存实现：

```bash
cd ~/coding/good-student
.venv/bin/python -m pytest tests/adapters -q
```
