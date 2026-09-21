# 本地模型部署与兼容接口 / Local model deployment

Ollama 提供部分 OpenAI API 兼容能力；常见本地接口地址形如 http://localhost:11434/v1。兼容格式不意味着每个模型都支持相同的工具调用、结构化输出或上下文长度。接入之前应分别验证普通对话、工具 schema、tool_call_id 和工具结果回传。

本地部署需要根据内存、显存和目标延迟选择模型与量化方式。不要在未知硬件上保证某个参数规模一定流畅。先用较小模型完成端到端功能验证，再依据测量结果提高模型规模或上下文窗口。

vLLM 也提供 OpenAI-compatible server，适合有条件搭建模型服务的环境。具体安装与 GPU 支持要查看当前官方说明。应用层最好只依赖 base_url、api_key、model 等明确配置，并对超时、非 JSON 响应和接口不支持给出可定位的错误。

无需模型的演示模式应明确标注为规则驱动：用于验证检索、审批、事件和报告流程，不等同于已经部署了本地大语言模型。真正的离线模型模式需要额外下载权重，并遵守对应许可证。

Keywords: Ollama vLLM local deployment OpenAI-compatible base_url model tool calling GPU quantization offline 本地模型 部署 显存 量化。

类型：本项目原创演示摘要；不包含模型权重，运行时不会自动下载模型。
参考：[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
参考：[vLLM server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
