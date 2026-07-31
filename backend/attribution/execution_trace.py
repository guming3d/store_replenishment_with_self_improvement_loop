"""Redacted Agent Framework execution-boundary tracing."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import Enum
from typing import Any

TraceEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credential",
    "connection_string",
}
_PRIVATE_REASONING_KEYS = {"reasoning", "thought", "chain_of_thought"}


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _message_roles(messages: Sequence[Any]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for message in messages:
        role = _string_value(getattr(message, "role", None)) or "unknown"
        roles[role] = roles.get(role, 0) + 1
    return roles


def _usage_payload(result: Any) -> dict[str, int | None] | None:
    usage = getattr(result, "usage_details", None)
    if not isinstance(usage, Mapping):
        return None
    keys = (
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "cache_creation_input_token_count",
        "cache_read_input_token_count",
        "reasoning_output_token_count",
    )
    return {key: usage.get(key) for key in keys if key in usage}


def _debug_payload(value: Any) -> Any:
    """Convert SDK values to JSON-safe data while removing secrets and reasoning."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _debug_payload(value.model_dump(mode="json"))
    if hasattr(value, "to_dict") and callable(value.to_dict) and not isinstance(value, type):
        try:
            serialized = value.to_dict(exclude={"raw_representation"})
        except TypeError:
            serialized = value.to_dict()
        return _debug_payload(serialized)
    if isinstance(value, Mapping):
        content_type = str(value.get("type", "")).lower()
        if "reasoning" in content_type or content_type in {"thought", "chain_of_thought"}:
            return {"type": value.get("type"), "redacted": "private_reasoning"}
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_KEYS or normalized in _PRIVATE_REASONING_KEYS:
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _debug_payload(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_debug_payload(item) for item in value]
    return repr(value)


class ExecutionTraceObserver:
    """Capture model and tool boundaries without prompts or private reasoning."""

    def __init__(self, emit: TraceEmitter, *, model: str, debug_raw_io: bool = False) -> None:
        self.emit = emit
        self.model = model
        self.debug_raw_io = debug_raw_io
        self.model_calls = 0
        self.tool_calls = 0

    async def record(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.emit(event_type, payload)

    def chat(self):
        from agent_framework import ChatContext, chat_middleware

        @chat_middleware
        async def trace_chat(context: ChatContext, call_next) -> None:
            self.model_calls += 1
            call_number = self.model_calls
            started = time.perf_counter()
            await self.record("MODEL_CALL_STARTED", {
                "model_call": call_number,
                "client": type(context.client).__name__,
                "configured_model": self.model,
                "message_count": len(context.messages),
                "message_roles": _message_roles(context.messages),
                "stream": context.stream,
                "option_keys": sorted((context.options or {}).keys()),
            })
            if self.debug_raw_io:
                await self.record("MODEL_RAW_INPUT", {
                    "model_call": call_number,
                    "messages": _debug_payload(context.messages),
                    "options": _debug_payload(context.options or {}),
                })
            try:
                await call_next()
            except Exception as exc:
                await self.record("MODEL_CALL_FAILED", {
                    "model_call": call_number,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "exception": type(exc).__name__,
                })
                raise
            result = context.result
            if self.debug_raw_io:
                await self.record("MODEL_RAW_OUTPUT", {
                    "model_call": call_number,
                    "response": _debug_payload(result),
                })
            await self.record("MODEL_CALL_COMPLETED", {
                "model_call": call_number,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "response_id": getattr(result, "response_id", None),
                "model": getattr(result, "model", None) or self.model,
                "finish_reason": _string_value(getattr(result, "finish_reason", None)),
                "usage": _usage_payload(result),
            })

        return trace_chat

    def function(self):
        from agent_framework import FunctionInvocationContext, function_middleware

        @function_middleware
        async def trace_function(context: FunctionInvocationContext, call_next) -> None:
            self.tool_calls += 1
            call_number = self.tool_calls
            started = time.perf_counter()
            function_name = getattr(context.function, "name", type(context.function).__name__)
            arguments = context.arguments if isinstance(context.arguments, Mapping) else {}
            await self.record("TOOL_CALL_STARTED", {
                "tool_call": call_number,
                "tool_name": function_name,
                "argument_keys": sorted(str(key) for key in arguments),
            })
            if self.debug_raw_io:
                await self.record("TOOL_RAW_INPUT", {
                    "tool_call": call_number,
                    "tool_name": function_name,
                    "arguments": _debug_payload(context.arguments),
                })
            try:
                await call_next()
            except Exception as exc:
                await self.record("TOOL_CALL_FAILED", {
                    "tool_call": call_number,
                    "tool_name": function_name,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "exception": type(exc).__name__,
                })
                raise
            if self.debug_raw_io:
                await self.record("TOOL_RAW_OUTPUT", {
                    "tool_call": call_number,
                    "tool_name": function_name,
                    "result": _debug_payload(context.result),
                })
            await self.record("TOOL_CALL_COMPLETED", {
                "tool_call": call_number,
                "tool_name": function_name,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "result_type": type(context.result).__name__,
            })

        return trace_function
