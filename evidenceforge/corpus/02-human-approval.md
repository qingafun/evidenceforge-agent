# 人工审批与安全恢复 / Human-in-the-loop approval

人工审批 Human-in-the-loop (HITL) 应发生在需要授权的动作之前。LangGraph 的 interrupt 可以暂停节点，通过 checkpointer 保存状态；使用相同 thread_id 和 Command(resume=...) 传回审批结果，才能继续原任务。

审批页面应显示待执行的具体参数，例如报告标题、输出内容摘要、目标路径。用户同意的是明确的一次动作，不能把一次批准扩大成以后所有动作的授权。拒绝也应作为可观察的终止状态，不能变成自动重试。

中断恢复时，包含 interrupt 的节点会从节点开头重新执行。不要把发邮件、扣费或文件写入安排在 interrupt 之前；需要外部副作用时，用幂等键和执行记录防止重复。演示项目可以把“写出最终报告”作为审批动作，再测试拒绝后确实没有生成报告文件。

Keywords: human approval interrupt resume reject checkpoint replay idempotency 人工审批 中断 恢复 拒绝 副作用。

类型：本项目原创演示摘要；不是实时官方文档。
参考：[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
