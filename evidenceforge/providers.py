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


class OutputTruncated(ModelError):
    """The provider exhausted its output allowance before completing a response."""


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

    There are at most two retries for transient transport failures, plus one
    regeneration with a larger allowance when provider output is truncated.
    Failed requests reserve their entire possible usage because a connection
    failure does not prove the provider did not generate a billed response.
    """

    def __init__(self, settings: Any, on_usage: Callable[[dict], None] | None = None,
                 *, client: httpx.Client | None = None):
        self.settings = settings
        self.on_usage = on_usage
        self._client = client
        self.last_response: dict[str, Any] | None = None
        self.metrics: dict[str, Any] = {
            "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "estimated_usage": False, "retries": 0, "failed_attempts": 0,
            "truncated_responses": 0,
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
                 json_mode: bool = False, *, max_output_tokens: int | None = None) -> dict:
        self.last_response = None
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
        requested_limit = (max_output_tokens if max_output_tokens is not None
                           else getattr(self.settings, "max_output_tokens", 4096))
        if type(requested_limit) is not int or not 1 <= requested_limit <= 32768:
            raise ModelError("The model output allowance must be an integer from 1 to 32768 tokens.")
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

        attempts = 0
        transient_failures = 0
        truncation_retries = 0
        previous_truncated_limit = 0
        while True:
            used = self.metrics["prompt_tokens"] + self.metrics["completion_tokens"]
            remaining = budget - used - prompt_estimate
            if remaining < 1:
                raise BudgetExceeded("This run has insufficient token budget for another model request.")
            output_limit = min(requested_limit, remaining)
            if previous_truncated_limit and output_limit <= previous_truncated_limit:
                raise BudgetExceeded(
                    "The model output was truncated and the remaining run budget cannot fund a larger "
                    "response. Increase EF_MAX_TOKENS or narrow the question."
                )
            payload["max_tokens"] = output_limit
            self.metrics["llm_calls"] += 1
            self.metrics["retries"] += int(attempts > 0)
            attempts += 1
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
                if retryable and transient_failures < 2:
                    time.sleep(0.2 * (2 ** transient_failures))
                    transient_failures += 1
                    continue
                raise ModelError(error) from None

            try:
                body = response.json()
                choice = body["choices"][0]
                message = choice["message"]
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
            valid_prompt = type(prompt) is int and prompt >= 0
            valid_completion = type(completion) is int and completion >= 0
            estimated = not (valid_prompt and valid_completion)
            reported_completion = completion if type(completion) is int and completion >= 0 else None
            if not valid_prompt:
                prompt = prompt_estimate
            if not valid_completion:
                # Visible text does not account for billed reasoning tokens.
                # Reserve the full allowance when measured output usage is absent.
                completion = output_limit
            finish_reason = choice.get("finish_reason")
            # Never propagate arbitrary provider fields or reasoning text. A few
            # compatible providers omit finish_reason; usage at the requested
            # ceiling is then the only reliable transport-level truncation signal.
            known_reasons = {"stop", "tool_calls", "function_call", "length", "max_tokens",
                             "max_output_tokens", "content_filter"}
            safe_reason = finish_reason if isinstance(finish_reason, str) and finish_reason in known_reasons else None
            truncated = (safe_reason in {"length", "max_tokens", "max_output_tokens"}
                         or (finish_reason is None and reported_completion is not None
                             and reported_completion >= output_limit))
            details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
            reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
            self.last_response = {
                "finish_reason": safe_reason, "output_limit": output_limit, "truncated": truncated,
                "prompt_tokens": prompt, "completion_tokens": completion,
                "reasoning_tokens": (reasoning_tokens if type(reasoning_tokens) is int
                                     and reasoning_tokens >= 0 else None),
            }
            if truncated:
                self.metrics["truncated_responses"] += 1
            self._record(prompt, completion, estimated=estimated)
            if self.metrics["prompt_tokens"] + self.metrics["completion_tokens"] > budget:
                raise BudgetExceeded("The provider response exhausted this run's token budget.")
            if safe_reason == "content_filter":
                raise ModelError(
                    "The model provider filtered this response. Rephrase the question or use a compatible provider."
                )
            if truncated:
                if truncation_retries >= 1 or output_limit >= 32768:
                    raise OutputTruncated(
                        "The model response is still truncated after increasing its output allowance. "
                        "Increase EF_MAX_OUTPUT_TOKENS and EF_MAX_TOKENS, choose a model with a larger "
                        "output limit, or narrow the question. No partial answer was accepted."
                    )
                truncation_retries += 1
                previous_truncated_limit = output_limit
                requested_limit = min(32768, output_limit * 2)
                continue
            return {"role": "assistant", "content": content, "tool_calls": normalized}
