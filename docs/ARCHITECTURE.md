# 架构与工程取舍

EvidenceForge 是一个单用户本地研究工作台。确定性工作流负责生命周期；真实模型在 research 节点内自主选工具、生成参数并决定何时停止。Planner、Researcher、Analyst、Critic 是同一模型服务上的角色分工，不是四个独立训练模型，也没有实现分布式多 Agent 通信。

```mermaid
flowchart LR
    UI[Web 工作台] --> API[FastAPI + SSE]
    API --> P[Planner]
    P --> H{人工审批}
    H -- 批准 --> R[Researcher 工具循环]
    H -- 拒绝 --> X[终止]
    R <--> T[Schema 工具注册表]
    T --> K[本地知识库 BM25 + TF-IDF + RRF]
    T --> W[可选 Tavily 搜索]
    T --> M[偏好记忆 / 受限计算器]
    R --> A[Analyst 报告]
    A --> C[Critic 引用与证据审查]
    C -- 一次有界修订 --> A
    C --> F[附加来源并导出]
    API <--> S[(SQLite 任务与事件)]
    P & H & R & A & C <--> CP[(LangGraph SQLite 检查点)]
    MCP[MCP stdio server] --> K
```

## 模块边界

| 模块 | 责任与关键点 |
|---|---|
| `api.py` | 严格请求 schema、后台执行、任务状态转换、可重连 SSE、同源写入限制 |
| `workflow.py` | 角色提示、结构化计划、人工 interrupt、工具循环、上下文裁剪、修订上限 |
| `providers.py` | 兼容 chat completions、超时与重试、预算预留、真实/估算用量区分、脱敏错误 |
| `tools.py` | 工具白名单、Pydantic 参数验证、安全算术 AST、可选固定服务网页检索 |
| `knowledge.py` | 原文分块与重叠、内容标识、Unicode 字符偏移、稀疏混合检索 |
| `store.py` | 任务/事件/显式偏好记忆；取消状态不可被迟到的 worker 覆盖 |
| `mcp_server.py` | SDK 提供的 stdio tools/resources 接口，不自行伪造协议 |
| `evaluation.py` | 标注检索 fixture、文档级 Recall@5 / MRR、可复现基线 |

## 关键实现

**RAG**：导入 Markdown/纯文本 → 保留原文分块 → BM25 与字符 n-gram TF-IDF → RRF 融合 → 有来源 ID 的上下文 → 报告引用。TF-IDF 是稀疏词法向量，不等于神经网络语义 embedding。本实现每次查询在小语料上计算排序，适合演示/个人资料，不宣称百万文档规模。

**两种记忆**：LangGraph 检查点是任务内状态；`memories` 表保存用户输入的偏好和选择保存的研究主题。偏好在规划/写作阶段读取，可以删除。研究历史摘要只记录主题与审查状态，不自动把模型结论升级为事实。

**人工介入**：计划提交后，`interrupt()` 先持久化状态。HTTP 审批使用条件状态更新，只有一个请求可以领取审批。`Command(resume=...)` 使用同一任务 ID 继续。拒绝终止；补充约束传给真实 Researcher 与 Analyst。离线模板不会理解反馈，仅保存审批记录。

**故障恢复**：启动时把未完成的 queued/running 任务标记为失败，页面可手动恢复。恢复使用已持久化的节点边界。整个 research 节点失败时，其工具循环会从该节点起点重做；当前预算统计保留，已发生请求可能重复计费。工具均只读，因此重放没有外部写入副作用。并不提供 exactly-once 执行保证。

**安全边界**：默认只绑定 loopback、限制 Host 与跨来源写入；工具无 shell、任意文件路径或任意 URL 抓取；模型和证据不能直接修改权限；计算器仅解释有限 AST 节点。提示注入无法仅靠提示词彻底解决，本实现通过受限工具降低影响，未提供完整对抗鲁棒性保证。

**可观测性**：事件记录节点、工具输入输出、耗时、错误与用量；不采集私有思维链。Token 上限是用量边界而非美元硬限额。响应缺少 usage 或请求失败时使用保守估算，并显示 estimated_usage。取消等待正在进行的 HTTP 请求返回，随后阻止后续节点。

## 当前限制与下一步

- 单进程 FastAPI BackgroundTasks + SQLite；多用户部署需要鉴权、持久任务队列、PostgreSQL 和资源隔离。
- 没有任意网页抓取、PDF OCR、代码执行沙箱、神经 embedding/reranker；可作为有评测支持的后续迭代。
- 没有模型训练/微调、强化学习、分布式 Agent 协议；本项目聚焦 Agent 应用工程。
- 引用 ID 完整率只能检查可解析性；模型审查也不是事实正确性的保证。
- 本地数据默认保存在 `data/`，不要将该目录、API Key 或私人导入资料提交到公开仓库。

## 原始资料

- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：工作流与自主 Agent 的区别及常用组合模式。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：检查点与跨任务记忆的不同职责。
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：审批中断、恢复与节点重放约束。
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：工具、资源、stdio 服务和客户端。
- [Ragas context recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)：参考材料与检索召回的评估概念。

代码为本项目实现；知识笔记是原创演示摘要，链接仅用于核对技术概念，不能理解为运行时已抓取最新页面。
