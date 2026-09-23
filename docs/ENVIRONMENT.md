# 环境配置与排错

## 推荐路线

首选 Python 3.12 + venv + 兼容模型 API。离线功能不需要 GPU、Docker、Node.js、向量数据库或付费服务。建议准备普通 4 核 CPU、8 GB 内存；这是宽松的开发配置建议，不是项目测得的最低配置。

项目在 Windows/Python 3.12 本地验证。CI 配置覆盖 Windows/Linux、Python 3.11/3.12；CI 是否通过以 GitHub Actions 为准。Dockerfile 已提供，本次本机 Docker 引擎未启动，不能声称完成容器实测。

## Windows

```powershell
git clone https://github.com/qingafun/evidenceforge-agent.git
cd evidenceforge-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m evidenceforge.cli doctor
.\.venv\Scripts\python.exe -m evidenceforge.cli serve
```

使用虚拟环境 Python 的完整路径，不必激活脚本，也无需更改 PowerShell 的执行策略。如果系统 `python` 打开微软商店，请安装 Python 3.11/3.12，并使用实际解释器路径执行 `-m venv .venv`。不要复制作者机器上的绝对路径。

## macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m evidenceforge.cli serve
```

浏览器访问 `http://127.0.0.1:8000`。全部数据在当前工作目录的 `data/`；建议从项目根目录启动，或设置 `EF_DATA_DIR` 为绝对路径。不要同时用多个服务进程操作同一个数据目录。

## 真实模型

复制 `.env.example` 为 `.env`，只在本地编辑密钥：

```dotenv
EF_API_KEY=你的密钥
EF_BASE_URL=https://服务商兼容接口地址/v1
EF_MODEL=服务商提供的模型名称
EF_MAX_TOKENS=64000
EF_MAX_OUTPUT_TOKENS=4096
EF_REQUEST_TIMEOUT=60
```

接口必须支持 `POST /chat/completions`、function tools，以及 `response_format: {"type":"json_object"}`。如果兼容接口不支持这两个特性，请改用支持的模型。`EF_BASE_URL` 是基础地址，不要再添加 `/chat/completions`。配置后重启服务，在页面选择真实模型模式。

只有设置 `EF_TAVILY_API_KEY` 才启用 `search_web`。否则模型基于本地知识库工作，不会假装已搜索互联网。输入任务、导入资料和相关记忆会被发送给你配置的模型服务；网页搜索查询会发送给 Tavily。

联网搜索配置：在 [Tavily 平台](https://app.tavily.com/)登录并复制 API Key；在项目根目录的 `.env` 中填写 `EF_TAVILY_API_KEY=你的密钥`，保留已配置的模型 API 项，然后重启服务。执行 `python -m evidenceforge.cli doctor` 应显示 `web_search_configured: true`；`/api/health` 的 `tools` 也应包含 `search_web`。页面要选择“模型驱动”，并在问题中说明需要联网检索。模型决定具体调用哪些工具；运行轨迹中的 `search_web` 事件才表示实际进行了网页搜索。Tavily 密钥通过 `Authorization: Bearer` 请求头发送。

Token 预算为单次研究任务的累计预算，包含失败请求的保守估算。兼容服务可能返回不同 usage 口径，实际账单以服务商为准。排错期间可减少 max_steps、缩短文档或选择成本较低的模型，先完成一个任务再批量评测。

`EF_MAX_OUTPUT_TOKENS` 是普通模型请求的输出额度，默认 4096；报告写作至少申请 8192。服务端还会按剩余任务预算收紧实际请求额度。检测到长度截断时最多额外重写一次，在剩余预算内提高额度；持续截断会使任务失败，不能把半截文字当作完成报告。推理模型可能把部分输出额度用于内部推理，正文短不代表本次输出 Token 消耗低。

离线演示只整理检索到的本地材料。内置语料覆盖 Agent 技术主题；研究其他主题应先导入相关 Markdown/TXT。模型模式在未配置 Tavily 时也只能检索本地资料。相关资料缺少可靠来源时，报告可给出带引用的“待核验”答案；没有足够相关资料时才标记“证据不足”。

## 本地模型可选路线

已安装 Ollama 的用户可设置 `EF_BASE_URL=http://localhost:11434/v1`、`EF_API_KEY=ollama`、`EF_MODEL=你已安装的模型名`。模型需要稳定支持工具调用与 JSON 输出。显存和内存取决于模型尺寸、量化和上下文长度，不能用单一配置保证所有模型运行。本项目没有捆绑模型权重，也未做本地模型性能实测。

## Docker 可选路线

```bash
docker compose up --build
```

宿主只映射 `127.0.0.1:8000`；数据使用命名卷；容器以普通用户运行。若使用宿主机 Ollama，需要按 Docker 平台设置可达的基础地址。

## 常见问题

| 现象 | 处理 |
|---|---|
| API 返回 422，模型未配置 | 检查 `.env` 位于启动目录，密钥与模型名非空，并重启 |
| HTTP 401/403 | 检查服务商、密钥权限与基础 URL；不要把 Key 放进截图或 Issue |
| HTTP 429 | 降低并发、等限流恢复；客户端最多重试两次 |
| 预算耗尽 | 先缩短问题/资料与工具次数；有必要再调整 EF_MAX_TOKENS |
| 输出截断或报告只有开头 | 查看轨迹中的模型结束原因与 Token 用量；确认模型支持写作所需输出额度，调整 EF_MAX_OUTPUT_TOKENS 及任务总预算后重新运行 |
| 显示“验收未通过” | 查看完整性、格式、证据检查与具体问题；当前报告是草稿，补充缺失资料或修正配置后重试 |
| 显示“待核验” | 已有可供回答的相关资料，但尚未独立核验；核对引用原文，不要把手工填写的来源名称当作官方确认 |
| 其他主题找不到答案 | 内置资料是 Agent 技术笔记；导入该主题的资料，或在模型模式配置 Tavily |
| 检索为空 | 导入相关原始资料；稀疏词法检索不能保证识别完全无共同字词的同义句 |
| 端口占用 | 使用 `evidenceforge serve --port 8001` |
| 服务重启任务失败 | 在原任务点击恢复，或对待审批任务继续审批 |
| Docker 无法连接 daemon | 启动 Docker Desktop；也可直接走 Python 路线 |

## MCP

把本项目虚拟环境内的 `evidenceforge-mcp` 可执行文件配置到支持 stdio MCP 的客户端。示意配置中的路径必须替换为你的实际绝对路径：

```json
{
  "mcpServers": {
    "evidenceforge": {
      "command": "D:/projects/evidenceforge-agent/.venv/Scripts/evidenceforge-mcp.exe",
      "env": {"EF_DATA_DIR": "D:/projects/evidenceforge-agent/data"}
    }
  }
}
```

暴露 `search_knowledge`、`read_source`、`list_research_runs`，以及 `evidenceforge://knowledge/stats` 资源。此功能是供外部客户端调用的 MCP server，本项目自身研究流程通过同一检索模块直接调用，不宣称已集成所有外部 MCP 服务。
