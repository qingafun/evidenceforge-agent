# RAG 检索增强与可追溯证据 / Retrieval-augmented generation

RAG 把外部知识检索结果送入生成阶段，用于补充模型参数中没有或不可靠的信息。工程流程包括文档导入、分块、索引、召回、证据整理和带引用的回答。检索系统找到相似片段，并不能证明片段中的结论真实。

分块需要在可读性和召回粒度之间权衡。重叠窗口保留边界上下文；稳定的 document_id、chunk_id、源路径和字符偏移让引用能够回到原文。不要为了摘要漂亮而修改被引用的原始片段。若同一文档多个重叠片段占满结果，应考虑按文档去重和限制上下文预算。

只有引用编号存在还不够，需要验证引用确实支持对应陈述。证据不足、内容互相矛盾或问题超出知识库时，应明确说明限制。离线演示中引用的是随项目提供的原创知识笔记，不代表已经检索到实时网页。

Keywords: RAG retrieval augmented generation evidence citation chunk overlap provenance grounded answer 检索 增强 引用 溯源 分块。

类型：本项目原创演示摘要；非实时抓取的网页内容。
参考：[RAG 原论文](https://arxiv.org/abs/2005.11401)
