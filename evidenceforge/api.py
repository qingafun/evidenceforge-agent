import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .bootstrap import seed_demo_once
from .config import Settings
from .knowledge import KnowledgeBase
from .store import Store
from .workflow import Engine


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunInput(StrictModel):
    question: str = Field(min_length=5, max_length=3000)
    mode: Literal["demo", "live"] = "demo"
    require_approval: bool = True
    max_steps: int = Field(default=8, ge=2, le=16)
    remember: bool = False


class ApprovalInput(StrictModel):
    approved: bool
    feedback: str = Field(default="", max_length=2000)


class DocumentInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=200000)
    source: str = Field(default="user", max_length=1000)


class MemoryInput(StrictModel):
    content: str = Field(min_length=1, max_length=2000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings.data_dir / "app.sqlite")
    knowledge = KnowledgeBase(settings.data_dir / "knowledge.sqlite")
    corpus = Path(__file__).parent / "corpus"
    if not corpus.exists():
        corpus = Path(__file__).parent.parent / "examples" / "knowledge"
    seed_demo_once(knowledge, corpus)
    engine = Engine(settings, store, knowledge)

    @asynccontextmanager
    async def lifespan(app):
        store.recover_interrupted()
        yield

    app = FastAPI(title="EvidenceForge", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.knowledge, app.state.engine = store, knowledge, engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are disabled."}, status_code=403)
            try:
                length = int(request.headers.get("content-length", 0))
            except ValueError:
                return JSONResponse({"detail": "Invalid content length."}, status_code=400)
            if length > 1_000_000:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    def get_run(run_id):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "任务不存在")
        return run

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "mode": "demo", "model": settings.model,
                "live_available": settings.live_available, "knowledge": knowledge.stats(),
                "tools": [t["function"]["name"] for t in engine.registry.schemas()]}

    @app.get("/api/runs")
    def list_runs():
        return store.list_runs()

    @app.post("/api/runs", status_code=201)
    def create_run(body: RunInput, tasks: BackgroundTasks):
        if body.mode == "live" and not settings.live_available:
            raise HTTPException(422, "真实模型未配置：请在 .env 设置 EF_API_KEY、EF_BASE_URL、EF_MODEL，然后重启。")
        if sum(r["status"] in ("running", "queued") for r in store.list_runs()) >= 3:
            raise HTTPException(429, "已有三个任务运行中，请等待或取消任务。")
        params = body.model_dump()
        question, mode = params.pop("question"), params.pop("mode")
        run = store.create_run(question, mode, params)
        tasks.add_task(engine.execute, run["id"])
        return run

    @app.get("/api/runs/{run_id}")
    def read_run(run_id: str):
        return get_run(run_id)

    @app.post("/api/runs/{run_id}/approve")
    def approve_run(run_id: str, body: ApprovalInput, tasks: BackgroundTasks):
        get_run(run_id)
        if not store.transition(run_id, ("awaiting_approval",), "queued"):
            raise HTTPException(409, "任务当前不在等待审批，可能已被处理。")
        tasks.add_task(engine.execute, run_id, approval=body.model_dump())
        return get_run(run_id)

    @app.post("/api/runs/{run_id}/resume")
    def resume_run(run_id: str, tasks: BackgroundTasks):
        get_run(run_id)
        if not store.transition(run_id, ("failed",), "queued"):
            raise HTTPException(409, "仅失败或重启中断的任务可以恢复；等待审批的任务请先审批。")
        tasks.add_task(engine.execute, run_id, resume=True)
        return get_run(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        get_run(run_id)
        if not store.transition(run_id, ("queued", "running", "awaiting_approval", "failed"), "cancelled"):
            raise HTTPException(409, "任务已经结束。")
        store.add_event(run_id, "system", "cancelled", "用户取消了任务；正在进行的请求将在返回后停止。")
        return get_run(run_id)

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str, after: int = 0):
        get_run(run_id)
        return store.events(run_id, max(0, after))

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0):
        get_run(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", 0)))
        except ValueError:
            cursor = max(0, after)

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = store.events(run_id, cursor)
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                status = store.get_run(run_id)["status"]
                if status not in ("running", "queued"):
                    # Drain events committed between the first query and status read.
                    for row in store.events(run_id, cursor):
                        cursor = row["id"]
                        yield f"id: {cursor}\nevent: trace\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                    yield f"event: status\ndata: {json.dumps({'status': status})}\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.35)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/report")
    def report(run_id: str):
        run = get_run(run_id)
        if not run["state"].get("report"):
            raise HTTPException(409, "报告尚未生成")
        draft = run["state"].get("report_draft") or run["state"].get("review", {}).get("passed") is False
        content = run["state"]["report"]
        if draft and "未通过验收的草稿" not in content:
            content = "> 未通过验收的草稿：内容尚未通过全部质量检查。\n\n" + content
        suffix = "-draft" if draft else ""
        return Response(content, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="evidenceforge-{run_id[:8]}{suffix}.md"'})

    @app.get("/api/documents")
    def documents():
        return knowledge.list_documents()

    @app.post("/api/documents", status_code=201)
    def add_document(body: DocumentInput):
        return knowledge.add_document(body.title, body.content, body.source)

    @app.delete("/api/documents/{document_id}", status_code=204)
    def delete_document(document_id: str):
        if not knowledge.delete_document(document_id):
            raise HTTPException(404, "文档不存在")
        return Response(status_code=204)

    @app.get("/api/memory")
    def memories():
        return store.list_memories()

    @app.post("/api/memory", status_code=201)
    def add_memory(body: MemoryInput):
        return store.add_memory(body.content)

    @app.delete("/api/memory/{memory_id}", status_code=204)
    def delete_memory(memory_id: str):
        if not store.delete_memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        return Response(status_code=204)

    @app.get("/api/evaluations")
    def evaluations():
        locations = [settings.data_dir / "evaluation.json", Path(__file__).parent.parent / "reports" / "retrieval-evaluation.json"]
        for path in locations:
            if path.is_file():
                return {"available": True, "results": json.loads(path.read_text(encoding="utf-8"))}
        return {"available": False, "results": None}

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    @app.get("/docs", include_in_schema=False)
    def api_docs():
        return FileResponse(static / "api.html")

    return app
