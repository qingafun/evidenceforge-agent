# 模型预算与有限工具循环 / Model budget

Agent 的预算不只是单次回答的 max_tokens。建议同时限制模型请求次数、工具调用轮数、总耗时、输入上下文长度和单次输出长度。预算用尽时应结束并解释未完成部分，而不是无限循环重试。

用模型返回的 usage 字段记录 prompt_tokens、completion_tokens 和 total_tokens。如果兼容接口没有 usage，只能标注为估算，不能把字符数近似值当作真实计费。价格由提供商和模型决定，应由配置提供，不把可能过期的价格写死为事实。

检索结果需要裁剪到上下文预算，但引用片段的原文与偏移仍应保留在存储层。适度压缩对话记忆可以控制输入增长，压缩时要保存用户约束、待审批动作和证据来源。

本地模型不产生云 API 账单，但仍消耗计算时间、内存与显存。增加 context length 通常需要更多资源。评估时应同时报告耗时、调用次数和任务结果，不能仅比较是否输出了一段文本。

Keywords: token budget max_tokens max steps timeout cost accounting usage bounded loop latency context window 模型预算 费用 令牌 上下文。

类型：本项目原创演示设计笔记；预算规则是应用策略，不是所有模型 API 的统一保证。
参考：[Ollama Context length](https://docs.ollama.com/context-length)
