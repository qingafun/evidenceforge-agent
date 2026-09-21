# 验证记录

验证日期：2026-09-21。环境：Windows、Python 3.12.14；浏览器验收使用本机 Edge（Playwright headless）。本页记录实际完成的检查，不将模拟模型响应计为真实模型成绩。

| 检查 | 实际结果 |
|---|---|
| Python 自动化测试 | **116 passed**，最终一次 15.44 秒；1 条 Starlette/AnyIO 依赖弃用警告 |
| Ruff | All checks passed |
| 前端脚本 | app.js / api.js 均通过 `node --check` |
| 依赖一致性 | `pip check`：No broken requirements found |
| 无密钥演示 | 完成报告；3 次本地工具调用、0 次模型请求、引用可解析 |
| 浏览器交互 | 审批、报告/证据/轨迹、Markdown 下载、文档与记忆 CRUD、评测页面通过 |
| 移动布局 | 390×844 视口，导航可用，无页面水平溢出 |
| 离线 API 文档 | 本地 OpenAPI 加载与筛选通过，不使用 CDN 脚本 |
| MCP | 真实 stdio initialize、list_tools、3 个工具调用及资源读取通过 |
| Python 打包 | wheel 构建成功；CI 从安装包而非源码目录运行 CLI 演示与评测 |
| GitHub CI | 配置 Windows/Linux × Python 3.11/3.12；实际结论见仓库 Actions |

## 重要测试场景

- 待审批状态在创建新服务实例后仍保留，同任务继续且不重复规划。
- 拒绝审批、运行前取消及工具返回后取消，均阻止后续动作。
- HTTP Mock 驱动完整真实模型适配器：模型选择知识检索、计算和读取来源，再写报告并审查。
- 模型写作请求失败后，重建 Engine 从检查点恢复，不重放已完成检索；用量保留，错误响应体不落入事件记录。
- 无效工具、未知参数、过量调用、超出预算、错误 JSON、危险计算表达式与网络错误边界。
- 文档按原文字符偏移保留证据，重复导入去重；删除隔离；删除内置示例后重启/API/MCP 不会补回。
- SSE 使用 Last-Event-ID 补齐事件；API 对不合法状态返回 409；未配置模型返回 422。
- 不可信 HTML 作为数据展示；跨来源写入拒绝；未配置 Tavily 时不暴露网页搜索工具。

## 检索评测

使用打包的 10 篇原创笔记和 20 个固定标注问题。在独立临时数据库运行，避免用户导入的资料污染结果。以召回 chunk 先按文档去重，再取前 5 个文档计算：

| 方法 | Recall@5 | MRR@5 |
|---|---:|---:|
| BM25 | 0.950 | 0.925 |
| Hybrid（BM25 + 字符 TF-IDF + RRF） | 0.950 | 0.925 |

完整结果在 `reports/retrieval-evaluation.json`。这是自建 smoke fixture，不是外部独立 benchmark；只测检索，不测生成正确性。英文转述的提示注入问题未召回目标笔记，保留该失败，不据此声称混合检索提升。

## 复现命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check evidenceforge tests evals
.\.venv\Scripts\python.exe -m evidenceforge.cli demo
.\.venv\Scripts\python.exe -m evidenceforge.cli evaluate
```

本次沙箱的系统临时目录权限受限，因此最终本地测试使用了 `-p no:cacheprovider --basetemp .test-temp-release-20260921`。普通环境可直接 `pytest -q`。此权限问题不是产品测试失败；最终所有功能用例已完整重新运行。

可选浏览器复现：安装 Playwright 与 Chromium 后执行 `node scripts/browser-smoke.cjs`。脚本会向本地服务添加测试研究任务；测试文档与记忆在流程末尾删除，研究记录保留。若用本机 Edge，设置 `EF_BROWSER_CHANNEL=msedge`。

## 未验证事项

- 未调用真实付费模型或 Tavily；模型输出质量、实际付费成本与在线检索效果待用户配置密钥后测量。
- 未实测本地模型推理速度、GPU 显存、量化效果。
- 本机 Docker 引擎未启动，Docker 配置未完成容器运行验证。
- 未进行多用户负载测试或完整提示注入对抗测试；此版本用于本地单用户场景。
