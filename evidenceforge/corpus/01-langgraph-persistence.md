# LangGraph 状态持久化与故障恢复 / Durable state

LangGraph 将任务分成节点、边和共享状态。Checkpointer 保存同一个 thread_id 的图状态，适合短期记忆、会话延续、人工中断和失败恢复。跨会话的偏好或长期知识应放在独立 store 中，不能把两类存储混为一谈。

重启后恢复任务的关键是持久化数据库、稳定的 thread_id，以及明确的恢复入口。InMemorySaver 只在当前进程中保存数据；进程退出后不能依靠它恢复。开发时可以采用 SQLite，扩展到多进程服务时需要重新评估数据库和任务调度。

恢复执行不等于副作用恰好执行一次。外部写操作应带幂等键，记录执行结果，并把准备步骤与执行步骤分开。测试应覆盖进程重启、节点失败后重试、不同任务之间的状态隔离。

Keywords: LangGraph persistence checkpoint thread_id SQLite restart recovery short-term memory long-term store idempotency.

类型：本项目原创演示摘要；不是官方文档全文，也不是运行时实时抓取结果。
参考：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
