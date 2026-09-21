"""Use the real stdio transport and MCP initialize/list/call handshake."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evidenceforge.config import Settings
from evidenceforge.knowledge import KnowledgeBase
from evidenceforge.mcp_server import build_server


def test_mcp_stdio_handshake_lists_and_calls_read_only_tools(tmp_path):
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "evidenceforge.mcp_server"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "EF_DATA_DIR": str(tmp_path), "EF_API_KEY": "", "EF_TAVILY_API_KEY": ""},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=20)) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "EvidenceForge"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "search_knowledge", "read_source", "list_research_runs",
                }
                result = await session.call_tool("search_knowledge", {"query": "LangGraph checkpoint", "limit": 2})
                assert not result.isError
                # FastMCP serializes list return values as one text block per item.
                chunks = [json.loads(block.text) for block in result.content if block.type == "text"]
                if len(chunks) == 1 and isinstance(chunks[0], list):
                    chunks = chunks[0]
                assert chunks
                assert "id" in chunks[0] and "text" in chunks[0]
                source = await session.call_tool("read_source", {"chunk_id": chunks[0]["id"]})
                assert not source.isError
                source_data = json.loads(source.content[0].text)
                assert source_data["id"] == chunks[0]["id"]
                invalid = await session.call_tool("search_knowledge", {"query": "checkpoint", "limit": 99})
                assert invalid.isError
                runs = await session.call_tool("list_research_runs", {})
                assert not runs.isError
                resource = await session.read_resource("evidenceforge://knowledge/stats")
                stats = json.loads(resource.contents[0].text)
                assert stats["documents"] > 0
                assert stats["chunks"] >= stats["documents"]

    asyncio.run(exercise())


def test_mcp_first_bootstrap_imports_all_examples_only_once(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="", tavily_api_key="", _env_file=None)
    build_server(settings)
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite")
    documents = knowledge.list_documents()
    assert len(documents) == 10
    for document in documents:
        assert knowledge.delete_document(document["id"])
    build_server(settings)
    assert knowledge.stats() == {"documents": 0, "chunks": 0}
