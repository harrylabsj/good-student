# 状态机（设计 §12）

## 错题候选状态

```text
received → extracting → needs_confirmation → {rejected | expired | confirmed}
extracting → extraction_failed → retrying → extracting
confirmed → analyzed → archived
```

只有 `confirmed` 可以进入分析。`rejected`、`expired`、`extraction_failed`
不得改变学生画像。临时区默认 24 小时过期。

## 知识点判断状态

```text
insufficient_evidence
    │ 已确认错题证据
    ▼
suspected_weakness
    │ 不同来源重复出错 / 新变式复测失败且无提示
    ▼
evidenced_weakness
    │ 练习 + 新变式复测通过且无提示
    ▼
improving
    │ 连续 ≥2 次独立（新变式、无提示）通过
    ▼
mastered ── 时间衰减（14 天）或新错误 ──▶ review_due
```

规则要点：

- 少于两条独立证据 → 置信度必须为 low。
- 订正后再次出现同类错误 → 强风险信号（risk=high）。
- 计划完成本身不改变能力状态；只有复测结果改变状态。
- 整场考试总分只进入科目概览，不复制给每个知识点。
