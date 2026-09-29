# -*- mode: python ; coding: utf-8 -*-
"""Windows build: include only application resources, never the checkout/data tree."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


root = Path(SPECPATH)
one_dir = os.environ.get("EF_BUILD_ONEDIR") == "1"

# Explicit allow-list of application assets. .env, data, logs, local-only notes,
# and documents imported by a user are not collected.
datas = collect_data_files("evidenceforge", includes=["static/*", "corpus/*.md"])
datas += collect_data_files("evals", includes=["retrieval.json"])
datas += [(str(root / "LICENSE"), "licenses")]

# Packages use metadata at runtime (versions, serializers, registered backends).
for distribution in (
    "evidenceforge", "langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite",
    "langgraph-prebuilt", "langgraph-sdk", "langchain-core", "langsmith", "mcp",
    "pywebview", "pythonnet", "uvicorn", "fastapi",
):
    datas += copy_metadata(distribution)

hiddenimports = [
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl", "uvicorn.lifespan.on",
    "webview.platforms.winforms", "webview.platforms.edgechromium",
    "clr", "pythonnet", "clr_loader", "sqlite3", "aiosqlite",
]
hiddenimports += collect_submodules("langgraph")
hiddenimports += collect_submodules("langchain_core", filter=lambda name: ".tracers" not in name)

analysis = Analysis(
    [str(root / "scripts" / "desktop-entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi", "gtk",
        "cefpython3", "webview.platforms.qt", "webview.platforms.gtk",
        "webview.platforms.cocoa", "webview.platforms.cef", "tkinter",
        "pytest", "ruff", "IPython", "notebook", "matplotlib",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
icon = root / "evidenceforge" / "static" / "desktop.ico"
exe_options = dict(
    name="EvidenceForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon) if icon.is_file() else None,
)
if one_dir:
    executable = EXE(pyz, analysis.scripts, [], exclude_binaries=True, **exe_options)
    bundle = COLLECT(
        executable, analysis.binaries, analysis.datas,
        strip=False, upx=False, name="EvidenceForge",
    )
else:
    executable = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], **exe_options)
