"""MCP tool loader — Exa + Tavily, both via native HTTP transport."""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

_TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
_EXA_KEY = os.environ.get("EXA_API_KEY", "")

_tools: list | None = None

logger = logging.getLogger("tinyagent.mcp")


async def init_mcp_tools() -> list:
    global _tools
    if _tools is not None:
        return _tools

    from langchain_mcp_adapters.client import MultiServerMCPClient

    servers: dict = {}

    if _TAVILY_KEY:
        servers["tavily"] = {
            "transport": "http",
            "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={_TAVILY_KEY}",
        }

    if _EXA_KEY:
        servers["exa"] = {
            "transport": "http",
            "url": "https://mcp.exa.ai/mcp",
            "headers": {"x-api-key": _EXA_KEY},
        }

    if not servers:
        logger.info("MCP: no API keys configured, skipping")
        _tools = []
        return _tools

    client = MultiServerMCPClient(servers)

    try:
        _tools = await asyncio.wait_for(client.get_tools(), timeout=15)
        logger.info("MCP: %d tools loaded", len(_tools))
    except Exception as exc:
        logger.warning("MCP failed: %s", exc)
        _tools = []

    return _tools


def get_mcp_tools() -> list:
    return _tools or []
