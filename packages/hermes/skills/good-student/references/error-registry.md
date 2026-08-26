# 错误与降级注册表（设计 §19）

| Codepath | 错误 | 处理 | 用户看到 |
|---|---|---|---|
| attachment intake | 无附件、空文件、格式不支持 | 不调用模型 | "没有可读取的错题材料" |
| host capability | 宿主无视觉/PDF 能力 | 请求文本、截图或手工输入 | 明确降级选项 |
| host LLM | 超时、限流、拒绝 | 宿主策略重试；最多一次产品级重试 | "识别暂时失败，数据未写入" |
| structured extraction | 空输出、非法 JSON、Schema 不匹配 | 修复提示重试一次，之后人工录入 | 显示失败字段，不显示伪结果 |
| partial batch | 部分页成功、部分页失败 | 保存成功候选，失败页单列 | "已识别 8/10 页" |
| dedupe | 重复上传 | 返回已有批次及差异 | "材料已处理，可继续确认" |
| confirmation | 用户中途退出 | 保留临时候选至过期 | "尚未进入学习画像" |
| persistence | 锁失败、磁盘满、库损坏 | 不覆盖原文件；失败退出 | 明确说明未保存 |
| analysis | 证据不足 | 返回 insufficient_evidence | "暂不能判断，需要复测" |
| taxonomy | 未知科目或知识点 | 保留自定义标签、降低置信度 | 请求用户确认 |
| report | 不可信 Markdown/HTML | 转义后渲染 | 安全文本 |

核心错误码（envelope `error.code`）：`schema_validation_failed`、`not_found`、
`invalid_argument`、`invalid_state`、`candidate_expired`、`confirm_phrase_mismatch`、
`persistence_error`。
