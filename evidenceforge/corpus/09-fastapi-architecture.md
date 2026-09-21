# FastAPI 服务分层与任务观测 / Service architecture

一个可调试的 Agent 服务可以分为 API、工作流、工具、模型适配器和存储五层。HTTP 层验证输入并返回 task_id；工作流管理节点和状态；工具封装能力；模型适配器统一兼容接口；存储持久化任务与证据。

FastAPI 的 async def 适合能够 await 的非阻塞 I/O。同步数据库查询或其他阻塞代码不能因为写在 async 函数里就自动变快；应根据调用方式使用普通 def、线程池或后台任务。CPU 密集型计算也需要独立考虑。

事件流可以显示节点开始、工具调用、证据召回、等待审批、完成与失败。持久化事件序号让前端重新连接后补齐遗漏信息。日志是用于解释行为的摘要，不应把内部推理过程、密钥或完整敏感上下文暴露给前端。

内存中的后台任务字典不能跨多个 worker 共享。演示部署可以明确采用单进程，扩展时再接入持久化任务队列、分布式锁与适当数据库。多个 worker 会带来独立内存副本，不能只增加进程数而忽略状态一致性。

Keywords: FastAPI async worker service API SSE events observability architecture background task 服务 架构 并发 事件 可观测性。

类型：本项目原创演示摘要；不是生产部署承诺。
参考：[FastAPI async](https://fastapi.tiangolo.com/async/)
参考：[FastAPI Deployment Concepts](https://fastapi.tiangolo.com/deployment/concepts/)
