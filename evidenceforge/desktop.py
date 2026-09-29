"""Windows desktop host: shared web UI, authenticated loopback API, native window."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .api import create_app
from .desktop_config import (
    ConnectionTestInput, DesktopSettingsInput, DesktopSettingsStore, ProfileLock, default_profile_dir,
)
from .tools import ToolRegistry
from .workflow import Engine

COOKIE = "evidenceforge_desktop"
STATIC = Path(__file__).parent / "static"


def create_desktop_app(profile_dir: Path, session_token: str):
    profile = DesktopSettingsStore(profile_dir)
    settings = profile.settings()
    app = create_app(settings)
    app.state.desktop = True
    app.state.desktop_profile = profile
    app.state.desktop_settings = settings
    configuration_lock = asyncio.Lock()
    settings_lock = threading.RLock()
    workers_drained = threading.Condition(settings_lock)
    stopping = threading.Event()
    workers = 0
    bootstrap_used = False

    # Each worker keeps the configuration with which it started, including
    # provider calls that are still returning after the user clicks Stop.
    def execute_snapshot(*args, **kwargs):
        nonlocal workers
        with settings_lock:
            if stopping.is_set():
                return
            engine = Engine(settings.model_copy(deep=True), app.state.store, app.state.knowledge, stop_event=stopping)
            workers += 1
        try:
            return engine.execute(*args, **kwargs)
        finally:
            with settings_lock:
                workers -= 1
                workers_drained.notify_all()

    app.state.engine.execute = execute_snapshot

    def active_runs():
        with settings_lock, app.state.store.connection() as connection:
            queued = connection.execute("SELECT count(*) FROM runs WHERE status IN ('running', 'queued')").fetchone()[0]
            return max(workers, queued)

    app.state.desktop_active_runs = active_runs

    def begin_shutdown():
        with settings_lock:
            stopping.set()
            with app.state.store.connection() as connection:
                connection.execute("UPDATE runs SET status = 'cancelled' WHERE status IN ('running', 'queued')")
                connection.commit()

    def wait_for_workers():
        # Uvicorn may abandon a thread after its grace period. Keep the profile
        # locked until that thread finishes its current request and DB writes.
        with workers_drained:
            workers_drained.wait_for(lambda: workers == 0)

    app.state.desktop_begin_shutdown = begin_shutdown
    app.state.desktop_wait_for_workers = wait_for_workers

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        # Pydantic's default 'input' field could echo an API key in a 422 body.
        return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                                        for item in error.errors()]}, status_code=422)

    @app.middleware("http")
    async def desktop_session(request: Request, call_next):
        nonlocal bootstrap_used
        if stopping.is_set():
            return JSONResponse({"detail": "程序正在退出，请稍后重新打开。"}, status_code=503)
        if request.url.path == "/desktop/launch" and request.method == "GET":
            if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
                return JSONResponse({"detail": "Invalid host"}, status_code=400)
            if bootstrap_used or not secrets.compare_digest(request.query_params.get("token", "").encode(), session_token.encode()):
                return JSONResponse({"detail": "请从桌面程序打开此页面。"}, status_code=403)
            bootstrap_used = True
            response = RedirectResponse("/desktop/settings" if profile.first_run else "/", status_code=303)
            response.set_cookie(COOKIE, session_token, httponly=True, samesite="strict")
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        if not secrets.compare_digest(request.cookies.get(COOKIE, "").encode(), session_token.encode()):
            return JSONResponse({"detail": "桌面会话已失效，请重新打开程序。"}, status_code=403)
        path = request.url.path
        if request.method in {"POST", "PUT"} and (path.startswith("/api/runs") or path == "/api/desktop/settings"):
            async with configuration_lock:
                response = await call_next(request)
        else:
            response = await call_next(request)
        if path.startswith("/api/desktop"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/desktop/settings", include_in_schema=False)
    def settings_page():
        return FileResponse(STATIC / "desktop-settings.html")

    @app.get("/api/desktop/settings", include_in_schema=False)
    def read_settings():
        return profile.public(settings, active_runs())

    @app.put("/api/desktop/settings", include_in_schema=False)
    def save_settings(body: DesktopSettingsInput):
        with settings_lock:
            if active_runs():
                raise HTTPException(409, "研究任务正在运行，请等待完成或停止任务后再保存配置。")
            values = body.model_dump()
            for key in ("api_key", "tavily_api_key"):
                if values[key] is None:
                    values[key] = getattr(settings, key)
            try:
                profile.save(values)
            except Exception:
                raise HTTPException(500, "无法保存加密配置，请检查当前用户对数据目录的写入权限。") from None
            for key, value in values.items():
                setattr(settings, key, value)
            app.state.engine.registry = ToolRegistry(app.state.knowledge, app.state.store, settings)
        return profile.public(settings)

    @app.post("/api/desktop/test", include_in_schema=False)
    def test_connection(body: ConnectionTestInput):
        key = settings.api_key if body.api_key is None else body.api_key
        if not key:
            raise HTTPException(422, "请先填写模型 API Key。")
        try:
            with httpx.Client(timeout=min(body.request_timeout, 20), follow_redirects=False) as client:
                response = client.post(body.base_url + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                       json={"model": body.model, "messages": [{"role": "user", "content": "Reply OK."}],
                                             "max_tokens": 32, "stream": False})
            if response.status_code != 200:
                descriptions = {401: "API Key 无效或已过期", 403: "该密钥没有访问权限",
                                404: "请检查服务地址和模型名称", 429: "请求受限或余额不足"}
                detail = descriptions.get(response.status_code, "请检查服务地址、模型名称或稍后重试")
                raise HTTPException(422, f"连接测试失败（HTTP {response.status_code}）：{detail}。")
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("choices"):
                raise ValueError("Invalid response")
        except HTTPException:
            raise
        except httpx.TimeoutException:
            raise HTTPException(422, "连接超时，请检查地址和网络后重试。") from None
        except Exception:
            raise HTTPException(422, "无法获得兼容接口响应，请检查服务地址和网络连接。") from None
        return {"ok": True, "message": "接口连接成功，已收到模型响应。研究时仍需模型支持工具调用与 JSON 输出。"}

    return app


class DesktopServer:
    def __init__(self, profile_dir: Path):
        self.token = secrets.token_urlsafe(32)
        self.app = create_desktop_app(profile_dir, self.token)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(128)
        self.listener.setblocking(False)
        self.origin = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        config = uvicorn.Config(self.app, log_config=None, access_log=False, loop="asyncio", http="h11",
                                ws="none", lifespan="on", timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.listener]}, daemon=True)

    @property
    def launch_url(self):
        return f"{self.origin}/desktop/launch?token={self.token}"

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 25
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("本地研究服务启动失败，请查看数据目录中的 logs/desktop.log。")
            time.sleep(0.05)

    def stop(self):
        self.app.state.desktop_begin_shutdown()
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=6)
        self.app.state.desktop_wait_for_workers()
        if self.thread.is_alive():
            self.thread.join()
        self.listener.close()


def self_test(server: DesktopServer, output: Path):
    """Exercise the actual packaged imports/assets/HTTP/workflow, with no model calls."""
    with httpx.Client(base_url=server.origin, follow_redirects=True, timeout=30, trust_env=False) as client:
        assert client.get("/api/desktop/settings").status_code == 403
        assert client.get(server.launch_url).status_code == 200
        health = client.get("/api/health").json()
        assert health["desktop"] and health["knowledge"]["documents"] > 0
        for file in ("app.js", "styles.css", "desktop-settings.js", "desktop-settings.css"):
            assert client.get("/static/" + file).status_code == 200
        public = client.get("/api/desktop/settings").json()
        assert "api_key" not in public and "tavily_api_key" not in public
        saved = server.app.state.desktop_profile.load()
        probe = {**saved, "api_key": "desktop-self-test-key", "tavily_api_key": "desktop-self-test-search"}
        client.put("/api/desktop/settings", json=probe).raise_for_status()
        stored = server.app.state.desktop_profile.path.read_text(encoding="utf-8")
        assert "desktop-self-test-key" not in stored and "desktop-self-test-search" not in stored
        assert server.app.state.desktop_profile.load()["api_key"] == "desktop-self-test-key"
        client.put("/api/desktop/settings", json=saved).raise_for_status()
        response = client.post("/api/runs", json={"question": "如何构建支持持久化、人工审批和评测的 RAG Agent？",
                                                "mode": "demo", "require_approval": True, "max_steps": 4})
        response.raise_for_status()
        run_id = response.json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "awaiting_approval":
                break
            time.sleep(0.1)
        assert run["status"] == "awaiting_approval" and run["state"]["plan"]["questions"]
        client.post(f"/api/runs/{run_id}/approve", json={"approved": True}).raise_for_status()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert run["status"] == "completed", run.get("error")
        assert client.get(f"/api/runs/{run_id}/report").status_code == 200
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"passed": True, "desktop": True, "frozen": bool(getattr(sys, "frozen", False)),
                                  "checks": ["auth", "bundled_assets", "corpus", "settings_redaction", "DPAPI", "approval", "demo_report"]}),
                      encoding="utf-8")


def run_window(server: DesktopServer, profile_dir: Path, smoke_output: Path | None = None):
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window("EvidenceForge · 研究工作台", server.launch_url, width=1440, height=960,
                                   min_size=(960, 700), background_color="#fafbf8", hidden=bool(smoke_output))
    completed = threading.Event()
    smoke_finished = threading.Event()
    smoke_passed = threading.Event()

    def closing():
        active = server.app.state.desktop_active_runs()
        return not active or window.create_confirmation_dialog("退出 EvidenceForge", "仍有研究任务在运行。现在退出会中断任务，确定退出吗？")

    window.events.closing += closing
    if smoke_output:
        def loaded():
            if completed.is_set():
                return
            completed.set()
            try:
                result = window.evaluate_js("({title:document.title, settings:!!document.querySelector('#desktop-settings-form, #settings-form'), workbench:!!document.getElementById('research-form')})")
                if not result or not (result.get("settings") or result.get("workbench")):
                    raise RuntimeError("WebView did not render the app")
                smoke_output.parent.mkdir(parents=True, exist_ok=True)
                smoke_output.write_text(json.dumps({"passed": True, "renderer": "edgechromium", **result}, ensure_ascii=False), encoding="utf-8")
                smoke_passed.set()
            finally:
                smoke_finished.set()
                window.destroy()

        window.events.loaded += loaded

        def watchdog():
            if not smoke_finished.wait(35):
                window.destroy()

        threading.Thread(target=watchdog, daemon=True).start()
    webview.start(gui="edgechromium", debug=False, private_mode=False, storage_path=str(profile_dir / "webview"),
                  icon=str(STATIC / "desktop.ico"))
    if smoke_output and not smoke_passed.is_set():
        raise RuntimeError("WebView2 窗口测试失败。请检查 Microsoft Edge WebView2 Runtime。")


def main():
    parser = argparse.ArgumentParser(description="EvidenceForge Desktop")
    parser.add_argument("--data-dir", type=Path, help="Override the desktop profile directory")
    tests = parser.add_mutually_exclusive_group()
    tests.add_argument("--self-test", type=Path, metavar="RESULT_JSON")
    tests.add_argument("--smoke-test", type=Path, metavar="RESULT_JSON")
    args = parser.parse_args()
    if (args.self_test or args.smoke_test) and not args.data_dir:
        parser.error("Self-tests require --data-dir pointing to a separate test profile.")
    profile_dir = (args.data_dir or default_profile_dir()).resolve()
    server = None
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        logs = profile_dir / "logs"
        logs.mkdir(exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(logs / "desktop.log", maxBytes=1_000_000,
                                                       backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        with ProfileLock(profile_dir):
            try:
                server = DesktopServer(profile_dir)
                server.start()
                if args.self_test:
                    self_test(server, args.self_test.resolve())
                else:
                    run_window(server, profile_dir, args.smoke_test.resolve() if args.smoke_test else None)
            finally:
                if server:
                    server.stop()
                    server = None
    except Exception as exc:
        logging.exception("Desktop startup failed")
        message = f"EvidenceForge 启动失败（{type(exc).__name__}）。\n请查看 {profile_dir / 'logs' / 'desktop.log'}\nWindows 桌面版需要 Microsoft Edge WebView2 Runtime。"
        if args.self_test or args.smoke_test:
            result = args.self_test or args.smoke_test
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps({"passed": False, "error_type": type(exc).__name__}), encoding="utf-8")
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc) if isinstance(exc, RuntimeError) else message, "EvidenceForge", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1
    finally:
        if server:
            server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
