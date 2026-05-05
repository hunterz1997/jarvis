"""
YouTube integration — calls youtube_mcp.py functions directly.
All 15 tools exposed: public (API key) + channel management (OAuth).
"""

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ── Bootstrap: inject YOUTUBE_API_KEY and add MCP path ───────────────────────
_YT_MCP_PATH = r"C:\Users\premj\OneDrive\Apps\AI Apps\MCP\YouTube"

def _ensure_env() -> None:
    """Load credentials from Claude Desktop config if not already in env."""
    if os.environ.get("YOUTUBE_API_KEY"):
        return
    try:
        import json
        cfg_path = r"C:\Users\premj\AppData\Roaming\Claude\claude_desktop_config.json"
        cfg = json.loads(open(cfg_path, encoding="utf-8").read())
        key = cfg["mcpServers"]["youtube"]["env"].get("YOUTUBE_API_KEY", "")
        if key:
            os.environ["YOUTUBE_API_KEY"] = key
    except Exception as e:
        logger.warning("Could not load YOUTUBE_API_KEY from config: %s", e)

_ensure_env()

if _YT_MCP_PATH not in sys.path:
    sys.path.insert(0, _YT_MCP_PATH)

# Lazy import — loaded once on first use
_yt = None

def _load() -> Any:
    global _yt
    if _yt is None:
        import importlib
        _yt = importlib.import_module("youtube_mcp")
    return _yt


class YouTubeIntegration:
    """Direct wrapper around youtube_mcp.py — no HTTP round-trip needed."""

    async def execute(self, tool_name: str, params: dict) -> Any:
        mod = _load()
        fn = getattr(mod, tool_name, None)
        if fn is None:
            return {"success": False, "error": f"Unknown YouTube tool: {tool_name}"}
        try:
            result = await fn(**params)
            return result  # already a formatted string from the MCP
        except Exception as e:
            logger.error("YouTube tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}


youtube = YouTubeIntegration()
