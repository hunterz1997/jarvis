"""
Jarvis Agent — ReAct loop (Think → Act → Observe → Repeat).
Streams tokens to the WebSocket and executes tools via the integration layer.
Backend-agnostic: works with Ollama (local) or Anthropic (cloud).
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from config import settings
from core.memory import memory
from core.model_router import select_model
from core.system_prompt import build_system_prompt
from core.tool_registry import get_tools
from core.tool_router import filter_tools
from core.llm import create_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool execution dispatcher
# ---------------------------------------------------------------------------

async def _execute_tool(tool_name: str, tool_input: dict) -> str:
    """Route a tool call to the correct integration and return the result as a string."""
    try:
        # Computer / system tools
        if tool_name in {
            "read_file", "write_file", "list_directory", "search_files",
            "file_operation", "run_command", "launch_application",
            "take_screenshot", "system_info", "clipboard", "run_python",
        }:
            from integrations.computer import computer
            result = await computer.execute(tool_name, tool_input)

        # Web tools
        elif tool_name == "web_search":
            from integrations.web import web_search
            result = await web_search(tool_input["query"], tool_input.get("max_results", 5))

        elif tool_name == "fetch_url":
            from integrations.web import fetch_url
            result = await fetch_url(tool_input["url"], tool_input.get("extract_text_only", True))

        # Google Drive
        elif tool_name.startswith("drive_"):
            from integrations.google_drive import drive
            result = await drive.execute(tool_name, tool_input)

        # OneDrive
        elif tool_name.startswith("onedrive_"):
            from integrations.onedrive import onedrive
            result = await onedrive.execute(tool_name, tool_input)

        # Gmail
        elif tool_name.startswith("gmail_"):
            from integrations.gmail import gmail
            result = await gmail.execute(tool_name, tool_input)

        # Google Calendar
        elif tool_name.startswith("calendar_"):
            from integrations.calendar import calendar
            result = await calendar.execute(tool_name, tool_input)

        # WhatsApp
        elif tool_name.startswith("whatsapp_"):
            from integrations.whatsapp import whatsapp
            result = await whatsapp.execute(tool_name, tool_input)

        # LinkedIn
        elif tool_name.startswith("linkedin_"):
            from integrations.linkedin import linkedin
            result = await linkedin.execute(tool_name, tool_input)

        # YouTube
        elif tool_name.startswith("youtube_"):
            from integrations.youtube import youtube
            result = await youtube.execute(tool_name, tool_input)

        # Zomato
        elif tool_name.startswith("zomato_"):
            from integrations.zomato import zomato
            result = await zomato.execute(tool_name, tool_input)

        # Scheduler tools
        elif tool_name == "schedule_task":
            from core.scheduler import scheduler
            run_at = tool_input.get("run_at")
            interval_minutes = tool_input.get("interval_minutes")
            task = await scheduler.create_task(
                name=tool_input["name"],
                prompt=tool_input["prompt"],
                interval_minutes=interval_minutes,
                run_at=run_at,
            )
            trigger_type = task.get("trigger_type", "recurring")
            if trigger_type == "once":
                msg = (
                    f"Reminder '{task['name']}' set for {task.get('run_at', 'the specified time')}. "
                    f"I'll execute the task and send you a notification at that time."
                )
            else:
                msg = (
                    f"Scheduled '{task['name']}' to run every {task['interval_minutes']} minutes. "
                    f"Results will appear as notifications in Jarvis."
                )
            result = {
                "success": True,
                "task_id": task["id"],
                "name": task["name"],
                "trigger_type": trigger_type,
                "interval_minutes": task.get("interval_minutes"),
                "run_at": task.get("run_at"),
                "message": msg,
            }

        elif tool_name == "list_schedules":
            from core.scheduler import scheduler
            tasks = await scheduler.list_tasks()
            if not tasks:
                result = {"success": True, "tasks": [], "message": "No scheduled tasks configured."}
            else:
                result = {"success": True, "tasks": tasks}

        elif tool_name == "cancel_schedule":
            from core.scheduler import scheduler
            ok = await scheduler.cancel_task(tool_input["task_id"])
            result = {
                "success": ok,
                "message": f"Scheduled task {tool_input['task_id']} cancelled." if ok else "Task not found.",
            }

        # Memory tools
        elif tool_name == "remember":
            await memory.set_preference(tool_input["key"], tool_input["value"])
            result = {"success": True, "message": f"Remembered: {tool_input['key']} = {tool_input['value']}"}

        elif tool_name == "recall":
            value = await memory.get_preference(tool_input["key"])
            result = {"success": True, "key": tool_input["key"], "value": value or "Not found"}

        else:
            result = {"success": False, "error": f"Unknown tool: {tool_name}"}

        return json.dumps(result) if isinstance(result, dict) else str(result)

    except Exception as e:
        logger.exception("Tool %s failed: %s", tool_name, e)
        return json.dumps({
            "success": False,
            "error": str(e),
            "suggestion": "The tool encountered an error. Try a different approach.",
        })


# ---------------------------------------------------------------------------
# Stream event types
# ---------------------------------------------------------------------------

class StreamEvent:
    @staticmethod
    def text(content: str) -> dict:
        return {"type": "text", "content": content}

    @staticmethod
    def tool_start(tool_name: str, tool_input: dict) -> dict:
        return {"type": "tool_start", "tool_name": tool_name, "tool_input": tool_input}

    @staticmethod
    def tool_end(tool_name: str, result: str) -> dict:
        return {"type": "tool_end", "tool_name": tool_name, "result": result[:500]}

    @staticmethod
    def model_info(model: str) -> dict:
        return {"type": "model_info", "model": model}

    @staticmethod
    def done() -> dict:
        return {"type": "done"}

    @staticmethod
    def error(message: str) -> dict:
        return {"type": "error", "message": message}


# ---------------------------------------------------------------------------
# Message history helpers
# ---------------------------------------------------------------------------

def _build_messages(
    history: list[dict],
    user_message: str,
    images: list[str] | None = None,
) -> list[dict]:
    """Convert DB history rows to simple role+content format.

    If `images` is provided (list of base64-encoded JPEGs without the data: prefix),
    the final user message becomes Anthropic-style content blocks containing both
    the image(s) and the text. The Anthropic backend handles vision natively;
    other backends will see only the text portion (graceful degradation).
    """
    messages = []
    for row in history:
        role = row["role"]
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": row["content"]})

    if images:
        blocks: list[dict] = []
        for b64 in images:
            # Strip data URL prefix if present (e.g. 'data:image/jpeg;base64,...')
            data = b64.split(",", 1)[1] if b64.startswith("data:") else b64
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
            })
        blocks.append({"type": "text", "text": user_message})
        messages.append({"role": "user", "content": blocks})
    else:
        messages.append({"role": "user", "content": user_message})
    return messages


def _append_assistant_turn(
    messages: list[dict],
    full_text: str,
    tool_calls: list[dict],
    anthropic_content: Any = None,
) -> None:
    """
    Append the assistant turn to message history in a format the backend understands.
    For Ollama (OpenAI format): uses tool_calls list.
    For Anthropic: uses the raw content blocks.
    """
    if anthropic_content is not None:
        # Anthropic backend — use the raw SDK content
        messages.append({"role": "assistant", "content": anthropic_content})
    elif tool_calls:
        # Ollama/OpenAI backend — build tool_calls format
        oai_tool_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["input"]),
                },
            }
            for tc in tool_calls
        ]
        msg: dict = {"role": "assistant", "tool_calls": oai_tool_calls}
        if full_text:
            msg["content"] = full_text
        messages.append(msg)
    else:
        messages.append({"role": "assistant", "content": full_text})


def _append_tool_results(
    messages: list[dict],
    tool_calls: list[dict],
    results: list[str],
    is_anthropic: bool = False,
) -> None:
    """Append tool results in the correct format for the backend."""
    if is_anthropic:
        # Anthropic format: user message with tool_result blocks
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result,
            }
            for tc, result in zip(tool_calls, results)
        ]
        messages.append({"role": "user", "content": tool_result_blocks})
    else:
        # OpenAI/Ollama format: individual tool messages
        for tc, result in zip(tool_calls, results):
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })


# ---------------------------------------------------------------------------
# Main Agent
# ---------------------------------------------------------------------------

class JarvisAgent:
    """ReAct agent that streams responses and executes tools. Backend-agnostic."""

    def __init__(self) -> None:
        self.backend = create_backend()
        self.tools = get_tools()
        self._backend_name = settings.llm_backend.lower()
        self._is_ollama = self._backend_name == "ollama"
        # Only filter tools for Ollama (tiny local model, limited context).
        # Anthropic and Groq can handle the full tool set.
        self._is_small_model = self._is_ollama

    def _model_display(self) -> str:
        backend = settings.llm_backend.lower()
        if backend == "groq":
            return f"groq:{settings.groq_model}"
        if backend == "ollama":
            return f"ollama:{settings.ollama_model}"
        return settings.sonnet_model

    async def run(
        self,
        session_id: str,
        user_message: str,
        images: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        start_time = time.monotonic()
        tools_used: list[str] = []

        # Load conversation history and preferences
        history = await memory.get_history(session_id)
        preferences = await memory.get_all_preferences()

        model_display = self._model_display()
        yield StreamEvent.model_info(model_display)

        # Model router: only for Anthropic backend (it selects opus vs sonnet)
        # Groq/Ollama always use the single configured model
        backend_name = settings.llm_backend.lower()
        if backend_name == "anthropic":
            selection = select_model(user_message, len(history) // 2)
            model_display = selection.model
            yield StreamEvent.model_info(model_display)
            max_tokens = selection.max_tokens
        else:
            max_tokens = 4096

        messages = _build_messages(history, user_message, images=images)
        # Persist only the text portion to history — images are ephemeral (large)
        # and would balloon the SQLite DB. The image is part of the active turn only.
        history_text = user_message + (f"\n[+{len(images)} image(s) attached]" if images else "")
        await memory.add_message(session_id, "user", history_text, model_used=model_display)

        # Compact system prompt only for Ollama (tiny local model, limited context)
        # Groq gets full prompt — it's a large model, compact would reduce quality
        system_prompt = build_system_prompt(preferences, compact=self._is_ollama)

        # Filter tools for Ollama AND Groq:
        #   Ollama — tiny context window; 55 tools overflow it
        #   Groq   — free-tier context budget; "Failed to call a function" with 55 tools
        active_tools = (
            filter_tools(self.tools, user_message)
            if self._is_small_model
            else self.tools
        )
        logger.debug("Active tools for query (%d/%d): %s",
                     len(active_tools), len(self.tools),
                     [t["name"] for t in active_tools])

        full_response_text = ""
        iteration = 0

        while iteration < settings.max_tool_iterations:
            iteration += 1
            current_text = ""
            tool_calls: list[dict] = []
            anthropic_content = None
            stop_reason = "end_turn"

            # Stream from backend
            async for event in self.backend.stream(
                system=system_prompt,
                messages=messages,
                tools=active_tools,
                max_tokens=max_tokens,
            ):
                etype = event.get("type")

                if etype == "text":
                    token = event["delta"]
                    current_text += token
                    full_response_text += token
                    yield StreamEvent.text(token)

                elif etype == "usage":
                    # Track token usage for cost display (fire-and-forget)
                    inp  = event.get("input_tokens", 0)
                    out  = event.get("output_tokens", 0)
                    mdl  = event.get("model", model_display)
                    cost = _calc_cost(mdl, inp, out)
                    asyncio.create_task(memory.track_usage(mdl, inp, out, cost))

                elif etype == "tool_calls":
                    tool_calls = event["calls"]

                elif etype == "_anthropic_content":
                    anthropic_content = event["content"]

                elif etype == "stop":
                    stop_reason = event["reason"]

                elif etype == "error":
                    err = event["message"]
                    # Friendly billing/auth error message
                    if "credit balance" in err.lower() or "billing" in err.lower():
                        yield StreamEvent.error(
                            "Anthropic account has insufficient credits. "
                            "Add credits at https://console.anthropic.com/settings/billing "
                            "or switch to LLM_BACKEND=ollama in .env"
                        )
                    elif "invalid" in err.lower() and "api" in err.lower():
                        yield StreamEvent.error(
                            "Invalid API key. Check ANTHROPIC_API_KEY in C:\\Claude\\Jarvis\\.env"
                        )
                    elif "connection" in err.lower() or "connect" in err.lower():
                        if self._is_ollama:
                            yield StreamEvent.error(
                                f"Cannot connect to Ollama at {settings.ollama_url}. "
                                "Make sure Ollama is running: open a terminal and run 'ollama serve'"
                            )
                        else:
                            yield StreamEvent.error(f"Connection error: {err}")
                    else:
                        yield StreamEvent.error(err)
                    return

            # Append assistant turn to message history
            _append_assistant_turn(messages, current_text, tool_calls, anthropic_content)

            if stop_reason == "end_turn":
                if full_response_text:
                    await memory.add_message(
                        session_id, "assistant", full_response_text, model_used=model_display
                    )
                break

            if stop_reason == "tool_use" and tool_calls:
                results = []
                for tc in tool_calls:
                    yield StreamEvent.tool_start(tc["name"], tc["input"])
                    result = await _execute_tool(tc["name"], tc["input"])
                    yield StreamEvent.tool_end(tc["name"], result)
                    tools_used.append(tc["name"])
                    results.append(result)
                    await memory.add_message(
                        session_id, "tool",
                        f"Called {tc['name']}",
                        tool_name=tc["name"],
                        tool_input=tc["input"],
                        tool_result=result,
                    )

                _append_tool_results(
                    messages, tool_calls, results,
                    is_anthropic=(not self._is_ollama),
                )
                continue

            break  # unexpected stop reason

        # Log task
        duration_ms = int((time.monotonic() - start_time) * 1000)
        await memory.log_task(
            session_id=session_id,
            task_type=_classify_task(user_message),
            description=user_message[:200],
            result_summary=full_response_text[:300] if full_response_text else "",
            tools_used=tools_used,
            model_used=model_display,
            duration_ms=duration_ms,
        )

        yield StreamEvent.done()


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------

def _classify_task(message: str) -> str:
    msg = message.lower()
    if any(k in msg for k in ("email", "gmail", "mail")):
        return "email"
    if any(k in msg for k in ("whatsapp", "message", "send")):
        return "messaging"
    if any(k in msg for k in ("calendar", "schedule", "meeting", "event")):
        return "calendar"
    if any(k in msg for k in ("drive", "document", "file", "folder")):
        return "files"
    if any(k in msg for k in ("order", "food", "biryani", "restaurant", "zomato")):
        return "food"
    if any(k in msg for k in ("linkedin", "post", "profile")):
        return "linkedin"
    if any(k in msg for k in ("youtube", "video", "channel")):
        return "youtube"
    if any(k in msg for k in ("search", "web", "google", "find", "weather", "news")):
        return "research"
    if any(k in msg for k in ("code", "script", "python", "run", "execute")):
        return "code"
    if any(k in msg for k in ("write", "draft", "report", "analyze", "summary")):
        return "writing"
    return "general"


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

# Pricing in USD per million tokens (input, output)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "opus":   (15.0, 75.0),
    "sonnet": (3.0,  15.0),
    "haiku":  (0.80,  4.0),
}

def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on model name and token counts."""
    model_lower = model.lower()
    in_price, out_price = 3.0, 15.0  # default: sonnet
    for key, prices in _MODEL_PRICING.items():
        if key in model_lower:
            in_price, out_price = prices
            break
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


# Module-level singleton
agent = JarvisAgent()
