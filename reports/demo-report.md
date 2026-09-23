> 核验状态：未核验。以下答案基于所列资料；来源名称与可解析引用不等于官方确认。

# 资料整理

研究问题：如何构建支持持久化、人工审批和评测的 RAG Agent？

> 离线演示：按规则整理本地资料，未调用大模型；未核验，不代表实时搜索或独立事实核查。

## 步骤参考（依次列出相关原文，需核对实际操作顺序）

1. \# RAG 检索增强与可追溯证据 / Retrieval-augmented generation RAG 把外部知识检索结果送入生成阶段，用于补充模型参数中没有或不可靠的信息。工程流程包括文档导入、分块、索引、召回、证据整理和带引用的回答。检索系统找到相似片段，并不能证明片段中的结论真实。 分块需要在可读性和召回粒度之间权衡。重叠窗口保留边界上下文；稳定的 document\_id、chunk\_id、源路径和字符偏移让引用能够回到原文。不要为了摘要漂亮而修改被引用的原始片段。若同一文档多个重叠片段占满结果，应考虑按文档去重和限制上下文预算。 只有引用编号存在还不够，需要验证引用确实支持对应陈述。证据不足、内容互相矛盾或问题超出知识库时，应明确说明限制。离线演示中引用的是随项目提供的原创知识笔记，不代表已经检索到实时网页。 Keywords: RAG retrieval augmented generation evidence citation chunk overlap provenance grounded answer 检索 增强 引用 溯源 分块。 类型：本项目原创演示摘要；非实时抓取的网页内容。 参考：［RAG 原论文］(https://arxiv.org/abs/2005.11401) [ev_8c5c45c3dc3630c2f52536a7]

2. \# Agent 工具安全与提示注入 / Tool security 知识库、网页和工具返回值都是数据。文档中出现“忽略系统要求”“把密钥发送到某网址”或“管理员已批准”，也不能升级为指令或权限。把证据包裹为结构化数据，并明确指示模型不执行其中的要求，可以降低风险，但不能单独构成安全保证。 权限控制要在模型之外实现：工具白名单、参数 schema、受限输出目录、最大调用次数、响应大小限制、超时和人工审批。工具调用必须经过服务端校验，不能直接把模型产生的路径交给任意文件写入函数。 外部 URL 抓取还涉及 SSRF：用户输入或模型生成的地址可能指向内网、回环地址或云元数据服务。仅做字符串过滤不足以覆盖重定向和 DNS 变化。小型项目可以先禁用任意 URL 抓取，让用户导入经过选择的文本。 红队测试应包含恶意文档、未知工具、路径穿越、重复审批、超预算循环，以及把隐藏指令伪装成引用来源的情况。日志不应保存 API Key。 Keywords: prompt injection untrusted data SSRF allowlist authorization path traversal security adversarial tool sandbox 提示注入 安全 白名单。 类型：本项目原创演示摘要；上述实施建议为本项目设计建议。 参考：［MCP Security Best Practices］(https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security\_best\_practices) [ev_6ae9ec9a9260fdda3d3279c9]

3. \# Agent 评估与可复现基线 / Evaluation 评估应拆开检索、生成和行动三个环节。检索题集需要问题与预期相关文档：Hit@k 统计前 k 个结果是否包含相关项，MRR 使用第一个正确结果名次的倒数。题目和答案应固定版本，避免每次随意更换测试样本。 回答评估关心正确性、引用覆盖、证据支持程度和不知道时是否拒答。仅检查答案带有 ［1］ 这样的编号，不足以验证它真的有证据支持。LLM-as-a-judge 可以补充人工检查，但评判模型也会有偏差，需要记录模型、提示与评分规则。 行动评估应验证审批是否生效、预算是否停止循环、失败能否恢复，以及恶意证据是否导致越权。通过率、延迟和 token usage 应分别记录，失败样本也应保留。 无需 API Key 的 deterministic demo 适合回归测试流程，但不能用它的通过率证明真实大模型推理能力。玩具语料上的检索结果是工程回归基线，不能冒充公开 benchmark 的领先成绩。连接真实模型后应重复同一评估，并单独报告结果。 Keywords: evaluation benchmark Hit@k MRR retrieval ablation groundedness faithfulness regression adversarial test agent metrics 评估 消融 基线。 类型：本项目原创演示摘要与评估设计建议。 参考：［Ragas 原论文］(https://arxiv.org/abs/2309.15217) [ev_3d5a052d23f7415429d5244b]

4. \# 人工审批与安全恢复 / Human-in-the-loop approval 人工审批 Human-in-the-loop (HITL) 应发生在需要授权的动作之前。LangGraph 的 interrupt 可以暂停节点，通过 checkpointer 保存状态；使用相同 thread\_id 和 Command(resume=...) 传回审批结果，才能继续原任务。 审批页面应显示待执行的具体参数，例如报告标题、输出内容摘要、目标路径。用户同意的是明确的一次动作，不能把一次批准扩大成以后所有动作的授权。拒绝也应作为可观察的终止状态，不能变成自动重试。 中断恢复时，包含 interrupt 的节点会从节点开头重新执行。不要把发邮件、扣费或文件写入安排在 interrupt 之前；需要外部副作用时，用幂等键和执行记录防止重复。演示项目可以把“写出最终报告”作为审批动作，再测试拒绝后确实没有生成报告文件。 Keywords: human approval interrupt resume reject checkpoint replay idempotency 人工审批 中断 恢复 拒绝 副作用。 类型：本项目原创演示摘要；不是实时官方文档。 参考：［LangGraph Interrupts］(https://docs.langchain.com/oss/python/langgraph/interrupts) [ev_d8815a9f0af5f500a4a765c6]


## 证据来源

- [ev_8c5c45c3dc3630c2f52536a7] RAG 检索增强与可追溯证据 / Retrieval-augmented generation — demo:03-rag-evidence.md
- [ev_6ae9ec9a9260fdda3d3279c9] Agent 工具安全与提示注入 / Tool security — demo:06-tool-security.md
- [ev_3d5a052d23f7415429d5244b] Agent 评估与可复现基线 / Evaluation — demo:08-evaluation.md
- [ev_d8815a9f0af5f500a4a765c6] 人工审批与安全恢复 / Human-in-the-loop approval — demo:02-human-approval.md
