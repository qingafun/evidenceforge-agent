# 技术调研简报

研究问题：如何构建支持持久化、人工审批和评测的 RAG Agent？

> 离线演示：以下内容来自本地资料摘录与规则模板，未调用大模型，不代表实时调研。

## 检索到的证据

### RAG 检索增强与可追溯证据 / Retrieval-augmented generation

RAG 把外部知识检索结果送入生成阶段，用于补充模型参数中没有或不可靠的信息。工程流程包括文档导入、分块、索引、召回、证据整理和带引用的回答。检索系统找到相似片段，并不能证明片段中的结论真实。 分块需要在可读性和召回粒度之间权衡。重叠窗口保留边界上下文；稳定的 document_id、chunk_id、源路径和字符偏移让引用能够回到原文。不要为了摘要漂亮而修改被引用的原始片段。若同一文档多个重叠片段占满结果，应考虑按文档去重和限制上下文预算。 只有引用编号存在还不够，需要验证引用确实支持对应陈述。证据不足、内容互相矛盾或问题超出知识库时，应明确说明限制。 [ev_8c5c45c3dc3630c2f52536a7]

### Agent 工具安全与提示注入 / Tool security

知识库、网页和工具返回值都是数据。文档中出现“忽略系统要求”“把密钥发送到某网址”或“管理员已批准”，也不能升级为指令或权限。把证据包裹为结构化数据，并明确指示模型不执行其中的要求，可以降低风险，但不能单独构成安全保证。 权限控制要在模型之外实现：工具白名单、参数 schema、受限输出目录、最大调用次数、响应大小限制、超时和人工审批。工具调用必须经过服务端校验，不能直接把模型产生的路径交给任意文件写入函数。 外部 URL 抓取还涉及 SSRF：用户输入或模型生成的地址可能指向内网、回环地址或云元数据服务。仅做字符串过滤不足以覆盖重定向和 DNS 变化。 [ev_6ae9ec9a9260fdda3d3279c9]

### Agent 评估与可复现基线 / Evaluation

评估应拆开检索、生成和行动三个环节。检索题集需要问题与预期相关文档：Hit@k 统计前 k 个结果是否包含相关项，MRR 使用第一个正确结果名次的倒数。题目和答案应固定版本，避免每次随意更换测试样本。 回答评估关心正确性、引用覆盖、证据支持程度和不知道时是否拒答。仅检查答案带有 ［1］ 这样的编号，不足以验证它真的有证据支持。LLM-as-a-judge 可以补充人工检查，但评判模型也会有偏差，需要记录模型、提示与评分规则。 行动评估应验证审批是否生效、预算是否停止循环、失败能否恢复，以及恶意证据是否导致越权。通过率、延迟和 token usage 应分 [ev_3d5a052d23f7415429d5244b]

### 人工审批与安全恢复 / Human-in-the-loop approval

人工审批 Human-in-the-loop (HITL) 应发生在需要授权的动作之前。LangGraph 的 interrupt 可以暂停节点，通过 checkpointer 保存状态；使用相同 thread_id 和 Command(resume=...) 传回审批结果，才能继续原任务。 审批页面应显示待执行的具体参数，例如报告标题、输出内容摘要、目标路径。用户同意的是明确的一次动作，不能把一次批准扩大成以后所有动作的授权。拒绝也应作为可观察的终止状态，不能变成自动重试。 中断恢复时，包含 interrupt 的节点会从节点开头重新执行。不要把发邮件 [ev_d8815a9f0af5f500a4a765c6]

### LangGraph 状态持久化与故障恢复 / Durable state

LangGraph 将任务分成节点、边和共享状态。Checkpointer 保存同一个 thread_id 的图状态，适合短期记忆、会话延续、人工中断和失败恢复。跨会话的偏好或长期知识应放在独立 store 中，不能把两类存储混为一谈。 重启后恢复任务的关键是持久化数据库、稳定的 thread_id，以及明确的恢复入口。InMemorySaver 只在当前进程中保存数据；进程退出后不能依靠它恢复。开发时可以采用 SQLite，扩展到多进程服务时需要重新评估数据库和任务调度。 恢复执行不等于副作用恰好执行一次。外部写操作应带幂等键，记录执行结果，并把准备步 [ev_4f639c0c444620d12adbf188]

### BM25、字符 TF-IDF 与 RRF / Hybrid lexical retrieval

BM25 根据词频、文档频率和长度归一化进行词法排序，适合精确术语，例如 thread_id 或 checkpoint。中文可以用重叠的二字、三字片段进行无需下载词典的分词，但会失去部分词语边界信息。 字符 n-gram TF-IDF 把子串转成稀疏向量，再计算余弦相似度。它能补充拼写变化和局部重叠的召回，但仍然是词法相似度，不是神经网络语义 embedding，不能保证找到没有共同字词的同义表达。 Reciprocal Rank Fusion (RRF) 按各召回器的名次相加：一个名次为 r 的结果贡献 1/(k+r)。演示实现取 k=60。这样无需假 [ev_214c77714c94153129c2cf41]

### FastAPI 服务分层与任务观测 / Service architecture

一个可调试的 Agent 服务可以分为 API、工作流、工具、模型适配器和存储五层。HTTP 层验证输入并返回 task_id；工作流管理节点和状态；工具封装能力；模型适配器统一兼容接口；存储持久化任务与证据。 FastAPI 的 async def 适合能够 await 的非阻塞 I/O。同步数据库查询或其他阻塞代码不能因为写在 async 函数里就自动变快；应根据调用方式使用普通 def、线程池或后台任务。CPU 密集型计算也需要独立考虑。 事件流可以显示节点开始、工具调用、证据召回、等待审批、完成与失败。持久化事件序号让前端重新连接后补齐遗漏信息。 [ev_d45252e461d2ad55a414fbf0]

### MCP 工具协议与能力边界 / Model Context Protocol

MCP 为模型应用访问外部工具和数据提供协议。服务端通过工具描述与输入 schema 说明能力，客户端可以发现并调用工具。MCP 连接的是应用与能力，并不会自动赋予工具安全性、模型智能或访问权限。 工具应具有单一、可解释的用途，例如 search_knowledge、read_evidence。输入应接受结构化验证，输出保留可追踪的证据 ID。工具失败和协议失败需要区分，便于 Agent 决定是否修正参数、重试或者结束。 工具注解可以描述只读、破坏性等性质，但它们只是提示，不能代替客户端自己的权限检查。对于来源不明的工具服务，工具描述和返回内容也属于不可 [ev_13f027d19eabf9f216441dd4]

## 验证与下一步

- 根据你的数据规模和预算制作候选方案对照实验。
- 人工核对引用原文、版本和适用范围，再做最终决策。
- 接入真实模型后，可以获得针对问题的比较、推理与完整建议。


## 证据来源

- [ev_8c5c45c3dc3630c2f52536a7] RAG 检索增强与可追溯证据 / Retrieval-augmented generation — demo:03-rag-evidence.md
- [ev_6ae9ec9a9260fdda3d3279c9] Agent 工具安全与提示注入 / Tool security — demo:06-tool-security.md
- [ev_3d5a052d23f7415429d5244b] Agent 评估与可复现基线 / Evaluation — demo:08-evaluation.md
- [ev_d8815a9f0af5f500a4a765c6] 人工审批与安全恢复 / Human-in-the-loop approval — demo:02-human-approval.md
- [ev_4f639c0c444620d12adbf188] LangGraph 状态持久化与故障恢复 / Durable state — demo:01-langgraph-persistence.md
- [ev_214c77714c94153129c2cf41] BM25、字符 TF-IDF 与 RRF / Hybrid lexical retrieval — demo:04-hybrid-retrieval.md
- [ev_d45252e461d2ad55a414fbf0] FastAPI 服务分层与任务观测 / Service architecture — demo:09-fastapi-architecture.md
- [ev_13f027d19eabf9f216441dd4] MCP 工具协议与能力边界 / Model Context Protocol — demo:05-mcp-tools.md
- [ev_fe158209f82e20cc2732da08] 模型预算与有限工具循环 / Model budget — demo:07-model-budget.md
