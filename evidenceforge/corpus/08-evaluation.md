# Agent 评估与可复现基线 / Evaluation

评估应拆开检索、生成和行动三个环节。检索题集需要问题与预期相关文档：Hit@k 统计前 k 个结果是否包含相关项，MRR 使用第一个正确结果名次的倒数。题目和答案应固定版本，避免每次随意更换测试样本。

回答评估关心正确性、引用覆盖、证据支持程度和不知道时是否拒答。仅检查答案带有 [1] 这样的编号，不足以验证它真的有证据支持。LLM-as-a-judge 可以补充人工检查，但评判模型也会有偏差，需要记录模型、提示与评分规则。

行动评估应验证审批是否生效、预算是否停止循环、失败能否恢复，以及恶意证据是否导致越权。通过率、延迟和 token usage 应分别记录，失败样本也应保留。

无需 API Key 的 deterministic demo 适合回归测试流程，但不能用它的通过率证明真实大模型推理能力。玩具语料上的检索结果是工程回归基线，不能冒充公开 benchmark 的领先成绩。连接真实模型后应重复同一评估，并单独报告结果。

Keywords: evaluation benchmark Hit@k MRR retrieval ablation groundedness faithfulness regression adversarial test agent metrics 评估 消融 基线。

类型：本项目原创演示摘要与评估设计建议。
参考：[Ragas 原论文](https://arxiv.org/abs/2309.15217)
