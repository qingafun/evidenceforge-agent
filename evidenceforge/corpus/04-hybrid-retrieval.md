# BM25、字符 TF-IDF 与 RRF / Hybrid lexical retrieval

BM25 根据词频、文档频率和长度归一化进行词法排序，适合精确术语，例如 thread_id 或 checkpoint。中文可以用重叠的二字、三字片段进行无需下载词典的分词，但会失去部分词语边界信息。

字符 n-gram TF-IDF 把子串转成稀疏向量，再计算余弦相似度。它能补充拼写变化和局部重叠的召回，但仍然是词法相似度，不是神经网络语义 embedding，不能保证找到没有共同字词的同义表达。

Reciprocal Rank Fusion (RRF) 按各召回器的名次相加：一个名次为 r 的结果贡献 1/(k+r)。演示实现取 k=60。这样无需假定 BM25 分数与余弦相似度可直接比较。融合分数只表示相对排序，不是真实性或正确率。

检索消融应在固定题集上分别测 BM25、TF-IDF、hybrid 的 Hit@k 和 MRR，并记录零结果情况。空查询或几乎没有词法重叠的查询应返回空结果，避免给生成模型塞入随机证据。

类型：本项目原创演示摘要；非实时文档。
参考：[BM25 参数](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity)
参考：[RRF](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)
