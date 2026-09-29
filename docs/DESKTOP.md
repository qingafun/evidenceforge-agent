# Windows 桌面版与 EXE 打包

桌面版复用网页版的页面、研究流程和数据接口，在独立 Windows 窗口中运行。研究工作台、计划审批、历史记录、证据、执行轨迹、知识库和报告导出保持一致；新增“连接设置”页面，用于填写模型 API Key、接口地址、模型名称和 Tavily 搜索 API Key。

## 使用桌面版

运行 `EvidenceForge.exe`。接收成品的电脑无需安装 Python、Node.js 或项目依赖；需要 **Windows 10/11 x64** 和 **Microsoft Edge WebView2 Runtime**。缺少运行时时，从 [微软官方 WebView2 下载页](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) 安装 Evergreen Standalone Installer 的 x64 版本，再重新打开程序。

在“连接设置”填写：

| 设置 | 填写方式 |
|---|---|
| 模型 API Key | 兼容模型服务商提供的密钥 |
| 接口地址 / Base URL | 服务商的兼容接口根地址，例如 `https://api.openai.com/v1`；不填写完整的 `/chat/completions` 路径 |
| 模型名称 | 服务商实际支持的模型 ID；需支持工具调用与 JSON 输出 |
| Tavily API Key | 可选；配置后模型驱动研究可调用联网搜索 |

保存后选择“模型驱动”开始研究。离线演示无需密钥；未配置 Tavily 时仅检索已导入的本地资料。API Key、模型服务与联网搜索分别配置，模型接口有密钥不表示已经能联网搜索。

桌面数据保存在 `%LOCALAPPDATA%\EvidenceForge`，通常是 `C:\Users\你的用户名\AppData\Local\EvidenceForge`。配置中的密钥使用 Windows DPAPI 按当前 Windows 用户加密保存。研究记录、资料和配置会在关闭窗口后保留，升级 EXE 也不必重新导入。

正在运行研究时，修改连接配置需等任务结束。关闭窗口会先询问是否停止研究；确认后阻止后续研究步骤，并等后台请求处理结束再释放数据目录。此期间再次打开可能提示“已在运行”，稍候再试即可。等待审批的计划会保留，下次打开仍可继续审批。

桌面版不会自动读取项目目录中的 `.env` 或 `data/`。首次使用需要在连接设置中填写信息；已有文件可在知识库页面重新导入。桌面数据目录与网页版分开，避免打包或启动时混入开发机器上的真实密钥和研究记录。配置的模型服务会接收研究问题和所需资料；Tavily 会接收搜索请求。

## 一键打包当前代码

打包机需安装 **Python 3.12 x64**（也支持 3.11 x64）。在项目根目录打开 Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
```

脚本从当前工作目录内的源码构建，尚未提交到 Git 的代码修改也会被包含。首次构建需要联网下载依赖；它会自动完成：

1. 检查 Python 版本与 64 位架构，并创建独立的 `.venv-build` 构建环境。
2. 安装 `requirements-lock.txt` 和 `requirements-desktop.txt` 中的固定版本，不改变原来的 `.venv`。
3. 使用 PyInstaller 打包 Python、后端、桌面窗口和网页资源。
4. 用独立测试数据目录启动成品 EXE，检查资源、后端、设置保存与离线研究流程。
5. 生成 `dist\EvidenceForge.exe` 及对应 `.sha256` 校验文件。

默认打包为单文件，无控制台窗口。首次启动需解压内置运行环境，可能比后续启动慢。构建会重建 `.venv-build`；该目录仅用于构建，不要在其中保存自己的文件。

```powershell
# 指定 Python；路径含空格或中文时使用引号
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Python "C:\Python312\python.exe"

# 依赖已经装好，仅重新打包当前修改
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -SkipInstall

# 目录版：启动更快，分发时需复制整个 dist\EvidenceForge 文件夹
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -OneDir

# 特殊构建环境无法运行自检时跳过；之后仍需在目标电脑验证
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -SkipInstall -SkipSmokeCheck
```

`-SkipInstall` 要求现有 `.venv-build` 已安装所需依赖；仍会安装当前项目和执行依赖一致性检查。单文件与目录版共用 `EvidenceForge.spec`。默认不启用 UPX，不需要管理员权限。

## 验证与排错

每次构建日志保存在 `build\logs\desktop-时间.log`，自动自检结果保存在 `build\desktop-check\时间\self-test.json`。构建或自检失败时脚本返回非零退出码，保留日志以便定位问题。

自动自检验证成品的 Python/后端和静态资源，不代表系统 WebView2 窗口已通过验证。可进一步运行真实窗口自检：

```powershell
$testRoot = Join-Path $env:TEMP ('EvidenceForge-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$testArguments = '--smoke-test "{0}" --data-dir "{1}"' -f (Join-Path $testRoot 'webview.json'), (Join-Path $testRoot 'data')
Start-Process .\dist\EvidenceForge.exe -ArgumentList $testArguments -WindowStyle Hidden -Wait
Get-Content (Join-Path $testRoot 'webview.json') -Encoding UTF8
```

这会以独立目录启动隐藏的真实 WebView2 窗口，检查页面后退出，不使用你的桌面研究记录。最后手动打开 EXE，检查连接设置、离线研究、计划审批和报告导出。

常见问题：

- **提示缺少 WebView2**：安装微软提供的 x64 Evergreen Runtime；只安装浏览器不一定等于运行时已经就绪。
- **Python 版本不支持**：使用 `-Python` 指定 3.11/3.12 的 x64 Python。程序接收方无需做这一步。
- **依赖下载失败**：确认能访问所用 Python 包镜像，重新执行构建；不要把下载未完成的环境当作 `-SkipInstall` 可用环境。
- **重新打包提示文件被占用**：关闭之前的 EvidenceForge 窗口后重试。
- **程序无法启动或界面空白**：查看桌面数据目录中的日志，并运行上述窗口自检。构建日志和运行日志用途不同。

## 打包范围

应用资源采用允许列表：`evidenceforge/static/` 页面与图标、`evidenceforge/corpus/` 内置演示笔记、`evals/retrieval.json` 标注数据，以及运行需要的第三方依赖与元数据。不会把 `.env`、`data/`、`.local-only/`、构建日志或用户导入的资料一起打包。

`dist/`、`build/` 和 `.venv-build/` 已忽略；成品 EXE 不提交进 Git。分发单文件版时只需复制 EXE（可附带 SHA256 文件与本文），目录版需复制整个输出目录。当前脚本不做代码签名，Windows 可能显示未知发布者；企业分发可另行使用受信任的代码签名证书。

实现依据：[pywebview 打包说明](https://pywebview.flowrl.com/guide/freezing)、[PyInstaller 文档](https://pyinstaller.org/en/stable/)、[WebView2 官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)。桌面依赖固定于 `requirements-desktop.txt`，便于复现和升级时逐项验证。
