"""Small OpenAI-compatible transport with explicit, per-run usage budgets.

Only public assistant content and tool calls leave this adapter. Provider error
bodies, credentials, and private reasoning fields are never returned or logged.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx


class ModelError(RuntimeError):
    """A sanitized, actionable model transport or response error."""


class BudgetExceeded(ModelError):
    """No request can be made within this run's configured token budget."""


def parse_json(content: str) -> Any:
    """Parse JSON, optionally wrapped in a single Markdown JSON code fence."""
    if not isinstance(content, str):
        raise ModelError("The model did not return JSON text.")
    content = content.strip()
    if content.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content, re.IGNORECASE)
        if not match:
            raise ModelError("The model returned an invalid JSON code fence.")
        content = match.group(1).strip()
    try:
        return json.loads(content)
    except (ValueError, RecursionError):
        raise ModelError("The model returned invalid JSON. Try again with a compatible model.") from None


def _estimate_tokens(value: Any) -> int:
    # A UTF-8 byte upper estimate deliberately favors avoiding budget overruns.
    # This is not tokenizer-measured usage; all fallbacks are labelled as estimates.
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(encoded) + 16


class ModelClient:
    """Chat-completions client; each instance owns one run's cumulative budget.

    There are at most two retries, for 429, 5xx, timeout, or connection failures.
    Failed requests reserve their entire possible usage because a connection
    failure does not prove the provider did not generate a billed response.
    """

    def __init__(self, settings: Any, on_usage: Callable[[dict], None] | None = None,
                 *, client: httpx.Client | None = None):
        self.settings = settings
        self.on_usage = on_usage
        self._client = client
        self.metrics: dict[str, Any] = {
            "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "estimated_usage": False, "retries": 0, "failed_attempts": 0,
        }

    def _record(self, prompt: int, completion: int, *, estimated: bool = False) -> None:
        self.metrics["prompt_tokens"] += prompt
        self.metrics["completion_tokens"] += completion
        self.metrics["estimated_usage"] = self.metrics["estimated_usage"] or estimated
        if self.on_usage:
            self.on_usage(dict(self.metrics))

    def _post(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        if self._client is not None:
            return self._client.post(url, headers=headers, json=payload,
                                     timeout=float(self.settings.request_timeout))
        with httpx.Client(timeout=float(self.settings.request_timeout), follow_redirects=False) as client:
            return client.post(url, headers=headers, json=payload)

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 json_mode: bool = False) -> dict:
        key = self.settings.api_key
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        if not key:
            raise ModelError("Configure an API key before using live model mode.")
        base_url = str(self.settings.base_url).rstrip("/")
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
        except ValueError:
            raise ModelError("Configure a valid HTTP(S) model base URL.") from None
        if parsed.scheme not in {"http", "https"} or not hostname or parsed.username:
            raise ModelError("Configure an HTTP(S) model base URL without embedded credentials.")
        if parsed.query or parsed.fragment:
            raise ModelError("The model base URL must not contain a query or fragment.")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            raise ModelError("Model messages must be a non-empty list of objects.")
        budget = int(self.settings.max_tokens)
        try:
            prompt_estimate = _estimate_tokens(messages) + (_estimate_tokens(tools) if tools else 0)
        except (TypeError, ValueError, RecursionError):
            raise ModelError("Model messages and tools must be JSON serializable.") from None
        payload: dict[str, Any] = {"model": self.settings.model, "messages": messages}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        for attempt in range(3):
            used = self.metrics["prompt_tokens"] + self.metrics["completion_tokens"]
            remaining = budget - used - prompt_estimate
            if remaining < 1:
                raise BudgetExceeded("This run has insufficient token budget for another model request.")
            output_limit = min(2000, remaining)
            payload["max_tokens"] = output_limit
            self.metrics["llm_calls"] += 1
            self.metrics["retries"] += int(attempt > 0)
            response = None
            retryable = False
            try:
                response = self._post(base_url + "/chat/completions", headers, payload)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                retryable = True
                error = "The model provider could not be reached. Check connectivity and timeout settings."
            except httpx.HTTPError:
                error = "The model request failed. Check the provider URL and connection settings."
            if response is not None and response.status_code != 200:
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                error = f"The model provider returned HTTP {response.status_code}. Check its configuration or retry later."
            if response is None or response.status_code != 200:
                self.metrics["failed_attempts"] += 1
                self._record(prompt_estimate, output_limit, estimated=True)
                if retryable and attempt < 2:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                raise ModelError(error) from None

            try:
                body = response.json()
                message = body["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []
                if not isinstance(content, str) or not isinstance(tool_calls, list):
                    raise ValueError
                normalized = []
                for call in tool_calls:
                    function = call["function"]
                    if (call.get("type") != "function" or not isinstance(call.get("id"), str)
                            or not isinstance(function.get("name"), str)
                            or not isinstance(function.get("arguments"), str)):
                        raise ValueError
                    normalized.append({"id": call["id"], "type": "function", "function": {
                        "name": function["name"], "arguments": function["arguments"],
                    }})
            except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
                self._record(prompt_estimate, output_limit, estimated=True)
                raise ModelError("The model provider returned an invalid chat-completions response.") from None

            usage = body.get("usage") or {}
            prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
            estimated = not (type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0)
            if estimated:
                prompt = prompt_estimate
                completion = min(output_limit, _estimate_tokens({"content": content, "tool_calls": normalized}))
            self._record(prompt, completion, estimated=estimated)
            if self.metrics["prompt_tokens"] + self.metrics["completion_tokens"] > budget:
                raise BudgetExceeded("The provider response exhausted this run's token budget.")
            return {"role": "assistant", "content": content, "tool_calls": normalized}
        raise ModelError("The model request failed after retrying.")
