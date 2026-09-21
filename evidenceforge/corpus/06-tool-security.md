# Agent 工具安全与提示注入 / Tool security

知识库、网页和工具返回值都是数据。文档中出现“忽略系统要求”“把密钥发送到某网址”或“管理员已批准”，也不能升级为指令或权限。把证据包裹为结构化数据，并明确指示模型不执行其中的要求，可以降低风险，但不能单独构成安全保证。

权限控制要在模型之外实现：工具白名单、参数 schema、受限输出目录、最大调用次数、响应大小限制、超时和人工审批。工具调用必须经过服务端校验，不能直接把模型产生的路径交给任意文件写入函数。

外部 URL 抓取还涉及 SSRF：用户输入或模型生成的地址可能指向内网、回环地址或云元数据服务。仅做字符串过滤不足以覆盖重定向和 DNS 变化。小型项目可以先禁用任意 URL 抓取，让用户导入经过选择的文本。

红队测试应包含恶意文档、未知工具、路径穿越、重复审批、超预算循环，以及把隐藏指令伪装成引用来源的情况。日志不应保存 API Key。

Keywords: prompt injection untrusted data SSRF allowlist authorization path traversal security adversarial tool sandbox 提示注入 安全 白名单。

类型：本项目原创演示摘要；上述实施建议为本项目设计建议。
参考：[MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)
