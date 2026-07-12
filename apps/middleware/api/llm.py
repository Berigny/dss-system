"""LLM integration for response generation."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, cast, Union

import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam

from config.settings import settings

# UPDATED PRICING (USD per Token)
# Matches your CURATED_MODELS list.
# Note: Estimates used where official pricing is dynamic/beta.
# UPDATED PRICING (USD per Token)
PRICING = {
    # Google: Gemini 2.5 Flash
    "google/gemini-2.5-flash": {"input": 0.000000075, "output": 0.0000003},
    
    # xAI: Grok 4.3 (current canonical id)
    "x-ai/grok-4.3": {"input": 0.000002, "output": 0.00001},
    
    # OpenAI: GPT-5.1
    "openai/gpt-5.1-chat": {"input": 0.000005, "output": 0.000015},
    
    # Anthropic: Haiku 4.5
    "anthropic/claude-haiku-4.5": {"input": 0.00000025, "output": 0.00000125},
    
    # --- ADD GEMMA MODELS HERE ---
    "google/gemma-4-26b-a4b-it": {"input": 0.0, "output": 0.0}, # OpenRouter Free tier
    "google/gemma-4-31b-it": {"input": 0.0000008, "output": 0.0000008}, 
    
    # Legacy/Fallbacks
    "openai/gpt-4o": {"input": 0.000005, "output": 0.000015},
    "anthropic/claude-3.5-sonnet": {"input": 0.000003, "output": 0.000015},
}


class LLMClient:
    """LLM client configured for OpenRouter via the OpenAI SDK."""

    def __init__(self):
        """Initialize the OpenAI client for local/OpenRouter endpoints."""
        local_base = (os.environ.get("LLM_BASE_URL") or "").strip()
        self.local_base = local_base
        self.default_local_model = os.environ.get("LLM_MODEL") or settings.LLM_MODEL
        local_api_key = os.environ.get("LLM_API_KEY") or ""
        openrouter_api_key = (
            os.environ.get("OPENROUTER_API_KEY")
            or settings.OPENROUTER_API_KEY
            or ""
        ).strip()

        self.local_client = None
        if self.local_base:
            self.local_client = openai.AsyncOpenAI(
                api_key=local_api_key,
                base_url=self.local_base,
            )

        self.openrouter_client = None
        if openrouter_api_key:
            self.openrouter_client = openai.AsyncOpenAI(
                api_key=openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        if not self.local_client and not self.openrouter_client:
            print("Warning: No LLM endpoint configured. Set LLM_BASE_URL or OPENROUTER_API_KEY.")

        configured_max_tokens = int(settings.LLM_MAX_TOKENS)
        disable_limits = os.getenv("DISABLE_RESPONSE_TOKEN_LIMITS", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.max_tokens: int | None = None if disable_limits else (
            configured_max_tokens if configured_max_tokens > 0 else None
        )
        self.supports_tools = os.getenv("LLM_SUPPORTS_TOOLS", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.force_system_signals = os.getenv("LLM_FORCE_SYSTEM_SIGNALS", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.extra_headers = {
            "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERRER", settings.API_BASE),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "ourIP.AI Assistant"),
        }
        self.signal_tools = [
            {
                "type": "function",
                "function": {
                    "name": "introspection_signal",
                    "description": "Receives runtime introspection signals for governance.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string"},
                            "governance": {
                                "type": "object",
                                "properties": {
                                    "L": {"type": "number"},
                                    "H": {"type": "number"},
                                    "P": {"type": "number"},
                                    "E": {"type": "number"},
                                    "U": {"type": "number"},
                                    "K": {"type": "number"},
                                    "A": {"type": "number"},
                                    "V": {"type": "number"},
                                    "V_mean": {"type": "number"},
                                    "V_std": {"type": "number"},
                                    "lawfulness_level": {"type": "number"},
                                    "cw": {"type": "number"},
                                    "eq6_commit_allowed": {"type": "number"},
                                    "drift": {"type": "number"},
                                },
                                "additionalProperties": True,
                            },
                            "hop": {"type": "integer"},
                            "phase": {"type": "string"},
                        },
                        "required": ["kind"],
                        "additionalProperties": True,
                    },
                },
            }
        ]

    @staticmethod
    def _is_model_not_found(exc: Exception) -> bool:
        text = str(exc).lower()
        return "model" in text and ("not found" in text or "404" in text)

    @staticmethod
    def _is_model_chat_unsupported(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "embedding",
            "does not support chat",
            "chat is not supported",
            "not support chat",
            "unsupported model",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_billing_error(exc: Exception) -> bool:
        """Detect OpenRouter no-credit / billing / rate-limit errors."""
        status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
        if status_code in (402, 429):
            return True
        text = str(exc).lower()
        markers = (
            "no available credit",
            "insufficient credit",
            "out of credit",
            "billing",
            "payment",
            "quota exceeded",
            "rate limit",
            "too many requests",
            "insufficient_quota",
        )
        return any(marker in text for marker in markers)

    def _billing_error_response(self, model: str) -> Dict:
        """Return a standardized, dashboard-friendly billing error payload."""
        return {
            "error": "provider_billing",
            "text": "OpenRouter account has no available credit. Add credit or check billing before retrying.",
            "detail": "https://openrouter.ai/settings/billing",
            "cost": 0,
            "tokens": {},
            "model": model,
        }

    async def _discover_default_local_model(self) -> str:
        if not self.local_base:
            return ""
        ollama_root = self.local_base[:-3] if self.local_base.endswith("/v1") else self.local_base
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{ollama_root}/api/tags")
                if response.status_code != 200:
                    return ""
                payload = response.json()
        except Exception:
            return ""

        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return ""
        ids: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("model") or item.get("name") or "").strip()
            if not mid:
                continue
            lowered = mid.lower()
            if "embed" in lowered or "embedding" in lowered or "bge-" in lowered:
                continue
            ids.append(mid)
        if not ids:
            return ""
        for candidate in ids:
            if "llama" in candidate.lower():
                return candidate
        return ids[0]

    @staticmethod
    def _is_online_model(model: str) -> bool:
        model_id = str(model or "").strip()
        if not model_id:
            return False
        if model_id.startswith("ollama/"):
            return False
        return "/" in model_id

    @staticmethod
    def _normalize_model(model: str, use_local: bool) -> str:
        model_id = str(model or "").strip()
        if use_local and model_id.startswith("ollama/"):
            return model_id[len("ollama/") :]
        if not use_local and model_id.startswith("openrouter/"):
            return model_id[len("openrouter/") :]
        return model_id

    def _resolve_client(self, requested_model: str) -> tuple[Optional[openai.AsyncOpenAI], bool, str]:
        wants_online = self._is_online_model(requested_model)
        if wants_online and self.openrouter_client:
            return self.openrouter_client, False, self._normalize_model(requested_model, use_local=False)
        if (not wants_online) and self.local_client:
            return self.local_client, True, self._normalize_model(requested_model, use_local=True)
        if self.openrouter_client:
            return self.openrouter_client, False, self._normalize_model(requested_model, use_local=False)
        if self.local_client:
            return self.local_client, True, self._normalize_model(requested_model, use_local=True)
        return None, False, str(requested_model or "").strip()

    async def generate_response(
        self,
        message: str,
        context: Optional[List[Dict]] = None,
        history: Optional[List[Dict]] = None,
        agent: str = settings.LLM_MODEL,
        system_prompt: str | None = None,
        signals: Optional[List[Dict]] = None,
    ) -> Dict:
        """Generate a response using local Ollama or OpenRouter based on model id."""
        client, is_local_client, selected_model = self._resolve_client(agent)
        if not client:
            return self._api_key_error()

        use_tool_signals = bool(signals) and self.supports_tools and not self.force_system_signals
        messages = self._prepare_messages(
            message,
            context,
            history,
            system_prompt=system_prompt,
            signals=signals,
            use_tool_signals=use_tool_signals,
        )
        request_kwargs = {
            "model": selected_model,
            "messages": messages,
            "tools": self.signal_tools if use_tool_signals else None,
            "tool_choice": "none" if use_tool_signals else None,
        }
        if self.max_tokens is not None:
            request_kwargs["max_tokens"] = self.max_tokens
        if not is_local_client:
            request_kwargs["extra_headers"] = self.extra_headers
        response: Any | None = None
        try:
            response = await client.chat.completions.create(**request_kwargs)
        except openai.APIError as exc:
            # In local-Ollama mode, recover from stale or unavailable model selections.
            if (
                is_local_client
                and (self._is_model_not_found(exc) or self._is_model_chat_unsupported(exc))
            ):
                fallback_model = self.default_local_model or await self._discover_default_local_model()
                if fallback_model and selected_model != fallback_model:
                    self.default_local_model = fallback_model
                    try:
                        fallback_kwargs = {
                            "model": fallback_model,
                            "messages": messages,
                            "tools": self.signal_tools if use_tool_signals else None,
                            "tool_choice": "none" if use_tool_signals else None,
                        }
                        if self.max_tokens is not None:
                            fallback_kwargs["max_tokens"] = self.max_tokens
                        if not is_local_client:
                            fallback_kwargs["extra_headers"] = self.extra_headers
                        response = await client.chat.completions.create(**fallback_kwargs)
                        selected_model = fallback_model
                    except openai.APIError:
                        pass
            if response is not None:
                pass
            elif use_tool_signals:
                try:
                    retry_kwargs = {
                        "model": selected_model,
                        "messages": messages,
                    }
                    if self.max_tokens is not None:
                        retry_kwargs["max_tokens"] = self.max_tokens
                    if not is_local_client:
                        retry_kwargs["extra_headers"] = self.extra_headers
                    response = await client.chat.completions.create(**retry_kwargs)
                except openai.APIError as retry_exc:
                    self.supports_tools = False
                    messages = self._prepare_messages(
                        message,
                        context,
                        history,
                        system_prompt=system_prompt,
                        signals=signals,
                        use_tool_signals=False,
                    )
                    try:
                        final_kwargs = {
                            "model": selected_model,
                            "messages": messages,
                        }
                        if self.max_tokens is not None:
                            final_kwargs["max_tokens"] = self.max_tokens
                        if not is_local_client:
                            final_kwargs["extra_headers"] = self.extra_headers
                        response = await client.chat.completions.create(**final_kwargs)
                    except openai.APIError as final_exc:
                        if self._is_billing_error(final_exc):
                            return self._billing_error_response(selected_model)
                        return {
                            "text": f"OpenRouter API error: {final_exc}",
                            "cost": 0,
                            "tokens": {},
                            "model": selected_model,
                        }
            else:
                if self._is_billing_error(exc):
                    return self._billing_error_response(selected_model)
                return {
                    "text": f"OpenRouter API error: {exc}",
                    "cost": 0,
                    "tokens": {},
                    "model": selected_model,
                }
        if response is None:
            return {
                "text": "OpenRouter API error: request failed before response creation",
                "cost": 0,
                "tokens": {},
                "model": selected_model,
            }

        usage = getattr(response, "usage", None) or {}
        finish_reason = None
        if getattr(response, "choices", None):
            finish_reason = getattr(response.choices[0], "finish_reason", None)

        # FIX: Allow 'default' to be None so we can check for missing costs safely
        def _get_usage_value(key: str, default: Union[int, float, None] = 0) -> Union[int, float, None]:
            if hasattr(usage, key):
                value = getattr(usage, key)
                if value is not None:
                    return value
            if isinstance(usage, dict):
                return usage.get(key, default)
            return default

        prompt_tokens = _get_usage_value("prompt_tokens") or 0
        completion_tokens = _get_usage_value("completion_tokens") or 0
        total_tokens = _get_usage_value("total_tokens") or 0
        
        # We pass None here to see if the API reported cost directly
        cost_from_usage = _get_usage_value("total_cost", None)

        # Fallback Calculation using your new PRICING dict
        pricing = PRICING.get(selected_model, {"input": 0, "output": 0})
        estimated_cost = (prompt_tokens * pricing["input"]) + (completion_tokens * pricing["output"])
        
        # Use OpenRouter's reported cost if available, otherwise use our estimate
        cost = cost_from_usage if cost_from_usage is not None else estimated_cost

        text = ""
        if getattr(response, "choices", None):
            first_choice = response.choices[0]
            message_obj = getattr(first_choice, "message", None)
            if isinstance(message_obj, dict):
                text = message_obj.get("content", "")
            else:
                text = getattr(message_obj, "content", "")

        return {
            "text": text,
            "cost": cost,
            "tokens": {"input": prompt_tokens, "output": completion_tokens, "total": total_tokens},
            "model": getattr(response, "model", selected_model),
            "finish_reason": finish_reason,
        }

    async def stream_response(
        self,
        message: str,
        context: Optional[List[Dict]] = None,
        history: Optional[List[Dict]] = None,
        agent: str = settings.LLM_MODEL,
        system_prompt: str | None = None,
        signals: Optional[List[Dict]] = None,
    ):
        client, is_local_client, selected_model = self._resolve_client(agent)
        if not client:
            async def _empty_stream():
                if False:
                    yield ""  # pragma: no cover
                return

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(self._api_key_error())
            return _empty_stream(), future

        use_tool_signals = bool(signals) and self.supports_tools and not self.force_system_signals
        messages = self._prepare_messages(
            message,
            context,
            history,
            system_prompt=system_prompt,
            signals=signals,
            use_tool_signals=use_tool_signals,
        )
        prompt_text = "".join(str(m.get("content", "")) for m in messages)
        loop = asyncio.get_running_loop()
        result_future = loop.create_future()
        requested_model = selected_model

        async def _stream():
            nonlocal prompt_text
            # Ensure prompt_text is always initialized within this scope.
            prompt_text = prompt_text or ""
            model_name = requested_model
            parts: list[str] = []
            finish_reason: str | None = None
            try:
                try:
                    stream_kwargs = {
                        "model": model_name,
                        "messages": messages,
                        "tools": self.signal_tools if use_tool_signals else None,
                        "tool_choice": "none" if use_tool_signals else None,
                        "stream_options": {"include_usage": True},
                        "stream": True,
                    }
                    if self.max_tokens is not None:
                        stream_kwargs["max_tokens"] = self.max_tokens
                    if not is_local_client:
                        stream_kwargs["extra_headers"] = self.extra_headers
                    stream = await client.chat.completions.create(**stream_kwargs)
                except openai.APIError as exc:
                    if (
                        is_local_client
                        and self._is_model_not_found(exc)
                        and self.default_local_model
                        and model_name != self.default_local_model
                    ):
                        model_name = self.default_local_model
                        fallback_stream_kwargs = {
                            "model": model_name,
                            "messages": messages,
                            "stream_options": {"include_usage": True},
                            "stream": True,
                        }
                        if self.max_tokens is not None:
                            fallback_stream_kwargs["max_tokens"] = self.max_tokens
                        if not is_local_client:
                            fallback_stream_kwargs["extra_headers"] = self.extra_headers
                        stream = await client.chat.completions.create(**fallback_stream_kwargs)
                    elif use_tool_signals:
                        self.supports_tools = False
                        fallback_messages = self._prepare_messages(
                            message,
                            context,
                            history,
                            system_prompt=system_prompt,
                            signals=signals,
                            use_tool_signals=False,
                        )
                        prompt_text = "".join(str(m.get("content", "")) for m in fallback_messages)
                        fallback_tool_kwargs = {
                            "model": model_name,
                            "messages": fallback_messages,
                            "stream_options": {"include_usage": True},
                            "stream": True,
                        }
                        if self.max_tokens is not None:
                            fallback_tool_kwargs["max_tokens"] = self.max_tokens
                        if not is_local_client:
                            fallback_tool_kwargs["extra_headers"] = self.extra_headers
                        stream = await client.chat.completions.create(**fallback_tool_kwargs)
                    else:
                        raise exc
                async for chunk in stream:
                    delta = None
                    if getattr(chunk, "choices", None):
                        choice = chunk.choices[0]
                        if getattr(choice, "finish_reason", None):
                            finish_reason = choice.finish_reason
                        delta_obj = getattr(choice, "delta", None)
                        if delta_obj is not None:
                            delta = getattr(delta_obj, "content", None)
                            if delta is None and isinstance(delta_obj, dict):
                                delta = delta_obj.get("content") or delta_obj.get("text")
                        if delta is None:
                            delta = getattr(choice, "text", None)
                    if not delta:
                        continue
                    parts.append(str(delta))
                    yield str(delta)
            except openai.APIError as exc:
                if self._is_billing_error(exc):
                    result_future.set_result(self._billing_error_response(model_name))
                else:
                    result_future.set_result(
                        {"text": f"OpenRouter API error: {exc}", "cost": 0, "tokens": {}, "model": model_name}
                    )
                return
            except Exception as exc:
                result_future.set_result(
                    {"text": f"LLM streaming error: {exc}", "cost": 0, "tokens": {}, "model": model_name}
                )
                return

            if not parts:
                response = await self.generate_response(
                    message=message,
                    context=context,
                    history=history,
                    agent=agent,
                    system_prompt=system_prompt,
                    signals=signals,
                )
                full_text = response.get("text") if isinstance(response, dict) else ""
                if full_text:
                    parts.append(full_text)
                    yield full_text
                result_future.set_result(response if isinstance(response, dict) else {})
                return

            full_text = "".join(parts)
            prompt_tokens = self._estimate_tokens(prompt_text)
            completion_tokens = self._estimate_tokens(full_text)
            total_tokens = prompt_tokens + completion_tokens
            pricing = PRICING.get(model_name, {"input": 0, "output": 0})
            cost = (prompt_tokens * pricing["input"]) + (completion_tokens * pricing["output"])
            result_future.set_result(
                {
                    "text": full_text,
                    "cost": cost,
                    "tokens": {"input": prompt_tokens, "output": completion_tokens, "total": total_tokens},
                    "model": model_name,
                    "finish_reason": finish_reason,
                }
            )

        return _stream(), result_future

    def _api_key_error(self) -> Dict:
        """Return a standardized error for a missing API key."""
        return {
            "text": "LLM endpoint not configured. Set LLM_BASE_URL or OPENROUTER_API_KEY.",
            "cost": 0,
            "tokens": {"input": 0, "output": 0},
            "model": "error",
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 4))

    def _prepare_messages(
        self,
        message: str,
        context: Optional[List[Dict]],
        history: Optional[List[Dict]],
        system_prompt: str | None = None,
        signals: Optional[List[Dict]] = None,
        use_tool_signals: bool = True,
    ) -> List[ChatCompletionMessageParam]:
        """Prepare the message list with context and history."""
        context_str = ""
        if context:
            context_items = []
            for item in context:
                text = ""
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                elif isinstance(item, str):
                    text = item
                if text:
                    context_items.append(f"- {text}")
            if context_items:
                context_str = (
                    "CONTEXT (retrieved library records; use as primary evidence when relevant):\n"
                    + "\n".join(context_items)
                )

        messages: List[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append(cast(ChatCompletionMessageParam, {"role": "system", "content": system_prompt}))
        if signals and use_tool_signals:
            for idx, signal in enumerate(signals, start=1):
                if not isinstance(signal, dict):
                    continue
                payload = json.dumps(signal, ensure_ascii=True, separators=(",", ":"))
                tool_call_id = f"introspection-signal-{idx}"
                messages.append(
                    cast(
                        ChatCompletionMessageParam,
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": tool_call_id,
                                    "type": "function",
                                    "function": {
                                        "name": "introspection_signal",
                                        "arguments": payload,
                                    },
                                }
                            ],
                        },
                    )
                )
                messages.append(
                    cast(
                        ChatCompletionMessageParam,
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": payload,
                        },
                    )
                )
        elif signals:
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                payload = json.dumps(signal, ensure_ascii=True, separators=(",", ":"))
                messages.append(
                    cast(
                        ChatCompletionMessageParam,
                        {
                            "role": "system",
                            "content": f"SYSTEM SIGNAL JSON (not user input): {payload}",
                        },
                    )
                )
        if history:
            for msg in history[-10:]:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                if role in ["user", "assistant"] and content:
                    # FIX: Explicit cast to satisfy Pylance's strict typing
                    messages.append(cast(ChatCompletionMessageParam, {"role": role, "content": content}))

        if context_str:
            messages.append(cast(ChatCompletionMessageParam, {"role": "assistant", "content": context_str}))
        messages.append(cast(ChatCompletionMessageParam, {"role": "user", "content": message or ""}))
        return messages


def _configure_openrouter_client(api_key: str) -> openai.AsyncOpenAI | None:
    key = str(api_key or "").strip()
    if key:
        return openai.AsyncOpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
        )
    return None


def set_openrouter_api_key(api_key: str) -> None:
    """Update the OpenRouter key used by the global LLM client at runtime."""
    from config.settings import settings

    key = str(api_key or "").strip()
    settings.OPENROUTER_API_KEY = key
    if key:
        os.environ["OPENROUTER_API_KEY"] = key
    elif "OPENROUTER_API_KEY" in os.environ:
        del os.environ["OPENROUTER_API_KEY"]
    llm.openrouter_client = _configure_openrouter_client(key)


def get_openrouter_api_key() -> str:
    """Return the currently effective OpenRouter API key."""
    from config.settings import settings

    return str(settings.OPENROUTER_API_KEY or "").strip()


# Instantiate the client globally.
# The app can start even if keys are missing; calls will fail gracefully.
llm = LLMClient()
