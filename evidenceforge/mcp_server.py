"""Read-only stdio MCP adapter. stdout is reserved for protocol messages."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .bootstrap import seed_demo_once
from .config import Settings
from .knowledge import KnowledgeBase
from .store import Store


def build_server(settings: Settings | None = None):
    settings = settings or Settings()
    settings.prepare()
    kb = KnowledgeBase(settings.data_dir / "knowledge.sqlite")
    corpus = Path(__file__).parent / "corpus"
    seed_demo_once(kb, corpus if corpus.exists() else Path(__file__).parent.parent / "examples" / "knowledge")
    store = Store(settings.data_dir / "app.sqlite")
    server = FastMCP("EvidenceForge")

    @server.tool()
    def search_knowledge(query: str, limit: int = 5) -> list[dict]:
        """Retrieve untrusted evidence excerpts from the local technical knowledge base."""
        if not 1 <= limit <= 10 or not 1 <= len(query) <= 1000:
            raise ValueError("Invalid query or limit")
        return kb.search(query, limit=limit)

    @server.tool()
    def read_source(chunk_id: str) -> dict:
        """Read a cited evidence chunk with its exact text and provenance."""
        return kb.read_chunk(chunk_id) or {"error": "Source not found"}

    @server.tool()
    def list_research_runs() -> list[dict]:
        """List local research task summaries without exposing internal model prompts."""
        return [{k: r[k] for k in ("id", "question", "status", "mode", "created_at")} for r in store.list_runs()]

    @server.resource("evidenceforge://knowledge/stats")
    def knowledge_stats() -> dict:
        """Knowledge corpus counts."""
        return kb.stats()

    return server


def main():
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
