import argparse
import json
from pathlib import Path

from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="EvidenceForge research agent workbench")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = sub.add_parser("demo", help="Run an offline research task without a server")
    demo.add_argument("--question", default="如何构建支持持久化、人工审批和评测的 RAG Agent？")
    demo.add_argument("--output", type=Path, default=Path("data/demo-report.md"))
    evaluate = sub.add_parser("evaluate", help="Evaluate retrieval on the bundled labeled fixture")
    evaluate.add_argument("--output", type=Path, default=Path("data/evaluation.json"))
    sub.add_parser("doctor", help="Check local configuration without exposing secrets")
    args = parser.parse_args()
    if args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            parser.error("Use a local bind address. Remote multi-user serving needs authentication first.")
        import uvicorn
        uvicorn.run("evidenceforge.api:create_app", factory=True, host=args.host, port=args.port)
        return
    if args.command == "evaluate":
        from .evaluation import evaluate
        result = evaluate(args.output)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        return
    settings = Settings()
    settings.prepare()
    if args.command == "doctor":
        import platform
        print(json.dumps({"python": platform.python_version(), "data_dir": str(settings.data_dir.resolve()),
                          "demo_ready": True, "live_configured": settings.live_available,
                          "model": settings.model, "web_search_configured": bool(settings.tavily_api_key)}, indent=2))
        return
    from .api import create_app
    app = create_app(settings)
    run = app.state.store.create_run(args.question, "demo", {"require_approval": False, "max_steps": 8, "remember": False})
    app.state.engine.execute(run["id"])
    result = app.state.store.get_run(run["id"])
    if result["status"] != "completed":
        raise SystemExit(result.get("error") or result["status"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result["state"]["report"], encoding="utf-8")
    print(f"Report: {args.output.resolve()}")
    print(json.dumps(result["state"]["metrics"], indent=2))


if __name__ == "__main__":
    main()
