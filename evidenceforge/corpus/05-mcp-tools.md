# MCP 工具协议与能力边界 / Model Context Protocol

MCP 为模型应用访问外部工具和数据提供协议。服务端通过工具描述与输入 schema 说明能力，客户端可以发现并调用工具。MCP 连接的是应用与能力，并不会自动赋予工具安全性、模型智能或访问权限。

工具应具有单一、可解释的用途，例如 search_knowledge、read_evidence。输入应接受结构化验证，输出保留可追踪的证据 ID。工具失败和协议失败需要区分，便于 Agent 决定是否修正参数、重试或者结束。

工具注解可以描述只读、破坏性等性质，但它们只是提示，不能代替客户端自己的权限检查。对于来源不明的工具服务，工具描述和返回内容也属于不可信输入。不要因为一个结果声称“用户已经批准”，就继续执行外部写入。

同一知识库可以同时通过 HTTP API 和 MCP 提供只读检索。后端复用业务代码，将协议适配与业务逻辑分离，避免维护两套检索实现。

Keywords: MCP Model Context Protocol tool discovery tools/list tools/call input schema read-only structured output 工具调用 协议。

类型：本项目原创演示摘要；此处引用固定协议版本，不宣称为最新版本。
参考：[MCP Tools 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
