"""
Unified LLM backend — supports both Anthropic (cloud) and Ollama (local/free).

Switch with LLM_BACKEND=ollama or LLM_BACKEND=anthropic in .env

Both backends yield the same StreamEvent dicts so agent.py is backend-agnostic.
"""

import json
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


# ── Tool schema conversion ────────────────────────────────────────────────────

def to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schema → OpenAI/Ollama function schema."""
    result = []
    for t in anthropic_tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def to_openai_messages(
    system: str,
    messages: list[dict],
) -> list[dict]:
    """
    Convert Jarvis internal message list (Anthropic format) to OpenAI format.

    Anthropic format tool use block:
      role=assistant, content=[{type:tool_use, id, name, input}]
    Anthropic format tool result block:
      role=user, content=[{type:tool_result, tool_use_id, content}]

    OpenAI format:
      role=assistant, tool_calls=[{id, type:function, function:{name, arguments}}]
      role=tool, tool_call_id=..., content=...
    """
    oai = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        # Plain text messages
        if isinstance(content, str):
            oai.append({"role": role, "content": content})
            continue

        # List of content blocks (Anthropic format)
        if isinstance(content, list):
            # Check if this is an assistant message with tool_use blocks
            tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]

            if tool_use_blocks:
                # Assistant tool call message
                text_content = " ".join(b.get("text", "") for b in text_blocks).strip() or None
                tool_calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b["input"]),
                        },
                    }
                    for b in tool_use_blocks
                ]
                oai_msg: dict = {"role": "assistant", "tool_calls": tool_calls}
                if text_content:
                    oai_msg["content"] = text_content
                oai.append(oai_msg)
                continue

            # Check if this is a user message with tool_result blocks
            tool_result_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_result_blocks:
                for b in tool_result_blocks:
                    result_content = b.get("content", "")
                    if isinstance(result_content, list):
                        result_content = " ".join(
                            r.get("text", "") for r in result_content if isinstance(r, dict)
                        )
                    oai.append({
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": str(result_content),
                    })
                continue

            # Regular assistant text blocks
            text = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
            if text:
                oai.append({"role": role, "content": text})
            continue

        # Anthropic SDK content objects (have .type attribute)
        if hasattr(content, "__iter__"):
            try:
                blocks = list(content)
                tool_use = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
                text_blks = [b for b in blocks if getattr(b, "type", None) == "text"]

                if tool_use:
                    text_content = " ".join(getattr(b, "text", "") for b in text_blks).strip() or None
                    tool_calls = [
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.input),
                            },
                        }
                        for b in tool_use
                    ]
                    oai_msg = {"role": "assistant", "tool_calls": tool_calls}
                    if text_content:
                        oai_msg["content"] = text_content
                    oai.append(oai_msg)
                    continue

                text = " ".join(getattr(b, "text", "") for b in text_blks)
                if text:
                    oai.append({"role": role, "content": text})
            except Exception:
                pass

    return oai


# ── Ollama Backend ────────────────────────────────────────────────────────────

class OllamaBackend:
    """
    Calls Ollama's OpenAI-compatible API at http://localhost:11434/v1
    Streams text tokens and returns complete tool calls.
    """

    def __init__(self, model: str, base_url: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                base_url=f"{self.base_url}/v1",
                api_key="ollama",  # required by openai library, not used by Ollama
                timeout=600.0,    # 10 min — first cold-load on low-RAM machines can be slow
            )
        return self._client

    async def check_available(self) -> tuple[bool, str]:
        """Return (is_available, message)."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    models = [m["name"] for m in data.get("models", [])]
                    if any(self.model in m for m in models):
                        return True, f"Model {self.model} ready"
                    else:
                        available = ", ".join(models) or "none"
                        return False, (
                            f"Model '{self.model}' not found in Ollama. "
                            f"Available: {available}. "
                            f"Run: ollama pull {self.model}"
                        )
                return False, f"Ollama returned HTTP {r.status_code}"
        except Exception as e:
            return False, f"Ollama not reachable at {self.base_url}: {e}"

    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
    ) -> AsyncGenerator[dict, None]:
        """
        Yield stream events:
          {"type": "text", "delta": str}
          {"type": "tool_calls", "calls": [{"id", "name", "input": dict}]}
          {"type": "stop", "reason": "end_turn" | "tool_use", "full_text": str}
        """
        client = self._get_client()
        oai_messages = to_openai_messages(system, messages)
        oai_tools = to_openai_tools(tools)

        # Accumulate text and tool calls across stream chunks
        full_text = ""
        tool_accum: dict[int, dict] = {}  # index → {id, name, arguments}
        finish_reason = "stop"

        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=oai_messages,
                tools=oai_tools if oai_tools else None,
                max_tokens=max_tokens,
                stream=True,
                temperature=0.6,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason or finish_reason

                # Text delta
                if delta.content:
                    full_text += delta.content
                    yield {"type": "text", "delta": delta.content}

                # Tool call delta accumulation
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_accum:
                            tool_accum[idx] = {
                                "id": tc.id or f"call_{idx}",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            tool_accum[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_accum[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_accum[idx]["arguments"] += tc.function.arguments

        except Exception as e:
            logger.error("Ollama stream error: %s", e)
            yield {"type": "error", "message": str(e)}
            return

        # Emit complete tool calls if any
        if tool_accum:
            calls = []
            for tc in tool_accum.values():
                try:
                    input_dict = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    input_dict = {}
                calls.append({
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": input_dict,
                })
            yield {"type": "tool_calls", "calls": calls}
            yield {"type": "stop", "reason": "tool_use", "full_text": full_text}
        else:
            yield {"type": "stop", "reason": "end_turn", "full_text": full_text}


# ── Groq Backend ─────────────────────────────────────────────────────────────

class GroqBackend:
    """
    Calls Groq's API — free, ultra-fast (LPU hardware).
    Uses the same OpenAI-compatible format as OllamaBackend.
    Models: llama-3.3-70b-versatile, llama-3.1-8b-instant, etc.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self._api_key,
                timeout=60.0,
                max_retries=0,  # we handle retries ourselves; avoid 40s+ wait on errors
            )
        return self._client

    async def check_available(self) -> tuple[bool, str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                if r.status_code == 200:
                    return True, f"Groq ready — model: {self.model}"
                elif r.status_code == 401:
                    return False, "Invalid Groq API key"
                return False, f"Groq returned HTTP {r.status_code}"
        except Exception as e:
            return False, f"Groq not reachable: {e}"

    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        oai_messages = to_openai_messages(system, messages)
        oai_tools = to_openai_tools(tools)

        full_text = ""
        tool_accum: dict[int, dict] = {}
        finish_reason = "stop"

        try:
            kwargs: dict = {
                "model": self.model,
                "messages": oai_messages,
                "max_tokens": max_tokens,
                "stream": True,
                "temperature": 0.6,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools
                kwargs["tool_choice"] = "auto"

            stream = await client.chat.completions.create(**kwargs)

            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason or finish_reason

                if delta.content:
                    full_text += delta.content
                    yield {"type": "text", "delta": delta.content}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_accum:
                            tool_accum[idx] = {"id": tc.id or f"call_{idx}", "name": "", "arguments": ""}
                        if tc.id:
                            tool_accum[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_accum[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_accum[idx]["arguments"] += tc.function.arguments

        except Exception as e:
            import re as _re
            err_str = str(e)
            logger.error("Groq stream error: %s", err_str)

            # Groq raises tool validation errors when llama-3.3 encodes the call as
            # "tool_name {json_args}" in the name field.  Parse and recover the call.
            if "tool call validation" in err_str.lower() and "attempted to call tool" in err_str:
                match = _re.search(r"attempted to call tool '(.+?)' which was not", err_str)
                if match:
                    raw_call = match.group(1).strip()
                    if " " in raw_call:
                        parts = raw_call.split(" ", 1)
                        try:
                            json.loads(parts[1])   # verify it's valid JSON
                            tool_accum[0] = {
                                "id": "call_recovered_0",
                                "name": parts[0],
                                "arguments": parts[1],
                            }
                            logger.warning(
                                "Recovered Groq tool call from error: %s(%s)",
                                parts[0], parts[1],
                            )
                        except json.JSONDecodeError:
                            yield {"type": "error", "message": err_str}
                            return
                    elif raw_call:
                        tool_accum[0] = {
                            "id": "call_recovered_0",
                            "name": raw_call,
                            "arguments": "{}",
                        }
                        logger.warning("Recovered bare Groq tool call from error: %s", raw_call)
                    else:
                        yield {"type": "error", "message": err_str}
                        return
                else:
                    yield {"type": "error", "message": err_str}
                    return
            else:
                yield {"type": "error", "message": err_str}
                return

        if tool_accum:
            calls = []
            for tc in tool_accum.values():
                name = tc["name"].strip()
                args_str = tc["arguments"]

                # Groq quirk: model sometimes puts "tool_name {json_args}" all in the name
                # e.g.  name='system_info {"info_type": "ram"}'  arguments=''
                if not args_str and " " in name:
                    parts = name.split(" ", 1)
                    maybe_args = parts[1]
                    try:
                        json.loads(maybe_args)   # validate it's real JSON
                        logger.warning(
                            "Groq tool name included args — split: name=%s args=%s",
                            parts[0], maybe_args,
                        )
                        name = parts[0]
                        args_str = maybe_args
                    except (json.JSONDecodeError, IndexError):
                        pass  # leave as-is

                try:
                    input_dict = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    input_dict = {}
                calls.append({"id": tc["id"], "name": name, "input": input_dict})
            yield {"type": "tool_calls", "calls": calls}
            yield {"type": "stop", "reason": "tool_use", "full_text": full_text}
        else:
            yield {"type": "stop", "reason": "end_turn", "full_text": full_text}


# ── Anthropic Backend ─────────────────────────────────────────────────────────

class AnthropicBackend:
    """
    Calls Anthropic's Claude API.
    Wraps the streaming response in the same event format as OllamaBackend.
    """

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    async def stream(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
    ) -> AsyncGenerator[dict, None]:
        full_text = ""
        final_message = None

        try:
            async with self._client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            ) as stream:
                async for event in stream:
                    if hasattr(event, "type") and event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            token = event.delta.text
                            full_text += token
                            yield {"type": "text", "delta": token}
                final_message = await stream.get_final_message()
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        # Emit token usage for cost tracking
        if hasattr(final_message, "usage") and final_message.usage:
            yield {
                "type":          "usage",
                "model":         self.model,
                "input_tokens":  final_message.usage.input_tokens,
                "output_tokens": final_message.usage.output_tokens,
            }

        if final_message.stop_reason == "tool_use":
            calls = []
            for block in final_message.content:
                if block.type == "tool_use":
                    calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            # Yield the raw content so agent can append it to messages correctly
            yield {"type": "_anthropic_content", "content": final_message.content}
            yield {"type": "tool_calls", "calls": calls}
            yield {"type": "stop", "reason": "tool_use", "full_text": full_text}
        else:
            yield {"type": "_anthropic_content", "content": final_message.content}
            yield {"type": "stop", "reason": "end_turn", "full_text": full_text}


# ── Factory ───────────────────────────────────────────────────────────────────

def create_backend():
    """Create the LLM backend based on LLM_BACKEND setting."""
    from config import settings
    backend = settings.llm_backend.lower()

    if backend == "groq":
        logger.info("LLM backend: Groq (%s)", settings.groq_model)
        return GroqBackend(api_key=settings.groq_api_key, model=settings.groq_model)

    elif backend == "ollama":
        logger.info("LLM backend: Ollama (%s @ %s)", settings.ollama_model, settings.ollama_url)
        return OllamaBackend(model=settings.ollama_model, base_url=settings.ollama_url)

    else:  # anthropic
        logger.info("LLM backend: Anthropic (%s)", settings.sonnet_model)
        return AnthropicBackend(
            api_key=settings.anthropic_api_key,
            model=settings.sonnet_model,
            max_tokens=settings.sonnet_max_tokens,
        )
