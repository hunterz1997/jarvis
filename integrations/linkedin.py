"""
LinkedIn integration — calls linkedin_mcp.py functions directly.
All 11 tools exposed. create_post and delete_post gate on confirmation.
"""

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ── Bootstrap ─────────────────────────────────────────────────────────────────
_LI_MCP_PATH = r"C:\Users\premj\OneDrive\Apps\AI Apps\MCP\Linkedin"

def _ensure_env() -> None:
    """Load LinkedIn access token from Claude Desktop config if not set."""
    if os.environ.get("LINKEDIN_ACCESS_TOKEN"):
        return
    try:
        import json
        cfg_path = r"C:\Users\premj\AppData\Roaming\Claude\claude_desktop_config.json"
        cfg = json.loads(open(cfg_path, encoding="utf-8").read())
        token = cfg["mcpServers"]["linkedin"]["env"].get("LINKEDIN_ACCESS_TOKEN", "")
        if token:
            os.environ["LINKEDIN_ACCESS_TOKEN"] = token
    except Exception as e:
        logger.warning("Could not load LINKEDIN_ACCESS_TOKEN from config: %s", e)

_ensure_env()

if _LI_MCP_PATH not in sys.path:
    sys.path.insert(0, _LI_MCP_PATH)

_li = None

def _load() -> Any:
    global _li
    if _li is None:
        import importlib
        _li = importlib.import_module("linkedin_mcp")
    return _li


# Tools that require explicit user confirmation before executing
_CONFIRM_REQUIRED = {"linkedin_create_post", "linkedin_delete_post", "linkedin_comment_on_post"}


def _is_pydantic_model(annotation: Any) -> bool:
    """Check if the annotation is a Pydantic BaseModel subclass."""
    try:
        from pydantic import BaseModel
        return isinstance(annotation, type) and issubclass(annotation, BaseModel)
    except Exception:
        return False


class LinkedInIntegration:
    """Direct wrapper around linkedin_mcp.py — no HTTP round-trip needed."""

    async def execute(self, tool_name: str, params: dict) -> Any:
        # Gate destructive actions on confirmation
        if tool_name in _CONFIRM_REQUIRED and not params.pop("confirmed", False):
            preview = {"tool": tool_name, "params": params}
            return {
                "success": False,
                "needs_confirmation": True,
                "preview": preview,
                "instruction": (
                    f"Show the user what will be posted/deleted and ask for explicit "
                    f"confirmation. Set confirmed=true once approved."
                ),
            }

        mod = _load()
        fn = getattr(mod, tool_name, None)
        if fn is None:
            return {"success": False, "error": f"Unknown LinkedIn tool: {tool_name}"}
        try:
            # Some MCP tools take a single Pydantic input model (newer style),
            # others take plain kwargs (older style). Detect and adapt.
            import inspect
            sig = inspect.signature(fn)
            sig_params = list(sig.parameters.values())
            if (len(sig_params) == 1
                    and sig_params[0].name == "params"
                    and _is_pydantic_model(sig_params[0].annotation)):
                ModelClass = sig_params[0].annotation
                result = await fn(ModelClass(**params))
            else:
                result = await fn(**params)
            return result
        except Exception as e:
            logger.error("LinkedIn tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}


linkedin = LinkedInIntegration()
