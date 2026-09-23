import json
from types import SimpleNamespace

import httpx
import pytest

from evidenceforge.providers import BudgetExceeded, ModelClient, ModelError, OutputTruncated, parse_json


def settings(**kwargs):
    values = dict(api_key="test-only-secret", base_url="https://provider.example/v1",
                  model="test-model", request_timeout=5, max_tokens=24000)
    return SimpleNamespace(**(values | kwargs))


def reply(content="A grounded result", **kwargs):
    return {"choices": [{"message": {"content": content, "role": "assistant"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12}} | kwargs


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_native_tools_json_mode_usage_and_no_reasoning_exposure():
    observed = []
    callbacks = []

    def handler(request):
        observed.append(json.loads(request.content))
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-secret"
        body = reply(None)
        body["choices"][0]["message"].update(
            reasoning_content="Do not persist private reasoning",
            tool_calls=[{"id": "call_1", "type": "function", "function": {
                "name": "search_knowledge", "arguments": '{"query":"memory"}',
            }}],
        )
        return httpx.Response(200, json=body)

    with mock_client(handler) as transport:
        model = ModelClient(settings(), callbacks.append, client=transport)
        result = model.complete([{"role": "user", "content": "Research memory"}],
                                tools=[{"type": "function", "function": {"name": "search_knowledge"}}],
                                json_mode=True)
    assert result["content"] == ""
    assert result["tool_calls"][0]["function"]["name"] == "search_knowledge"
    assert "reasoning_content" not in result
    assert observed[0]["max_tokens"] == 4096
    assert observed[0]["response_format"] == {"type": "json_object"}
    assert observed[0]["tool_choice"] == "auto"
    assert callbacks[0]["prompt_tokens"] == 50
    assert callbacks[0]["completion_tokens"] == 12
    assert not callbacks[0]["estimated_usage"]


def test_budget_preflight_makes_no_network_request():
    def handler(_):
        pytest.fail("An over-budget request must never reach the provider")

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_tokens=20), client=transport)
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "larger than the budget"}])
    assert model.metrics["llm_calls"] == 0


def test_cumulative_budget_and_requested_output_reservation():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply(usage={"prompt_tokens": 100, "completion_tokens": 90}))

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_tokens=200), client=transport)
        model.complete([{"role": "user", "content": "a"}])
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "a"}])
    assert len(requests) == 1
    assert 0 < requests[0]["max_tokens"] < 200
    assert model.metrics["prompt_tokens"] + model.metrics["completion_tokens"] == 190


def test_missing_usage_is_estimated_and_charged():
    with mock_client(lambda _: httpx.Response(200, json=reply(usage=None))) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "你好"}])
    assert model.metrics["estimated_usage"]
    assert model.metrics["prompt_tokens"] > 0
    assert model.metrics["completion_tokens"] == 4096


def test_partial_usage_keeps_measured_completion_including_hidden_reasoning():
    body = reply("Brief visible output", usage={"completion_tokens": 1200})
    with mock_client(lambda _: httpx.Response(200, json=body)) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["estimated_usage"]
    assert model.metrics["prompt_tokens"] > 0
    assert model.metrics["completion_tokens"] == 1200


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_errors_retry_twice_and_account_for_failed_attempts(status, monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(True)
        return httpx.Response(status, text="SECRET_PROVIDER_BODY") if len(calls) < 3 else httpx.Response(200, json=reply())

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 3
    assert model.metrics["failed_attempts"] == 2
    assert model.metrics["retries"] == 2
    assert model.metrics["estimated_usage"]
    assert model.metrics["completion_tokens"] == 8204


def test_timeout_retry_limit_and_sanitized_exception(monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)

    def handler(request):
        raise httpx.ReadTimeout("SECRET_TIMEOUT_DETAIL", request=request)

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError) as error:
            model.complete([{"role": "user", "content": "hello"}])
    assert "SECRET" not in str(error.value)
    assert model.metrics["llm_calls"] == 3


def test_nontransient_error_is_not_retried_or_exposed():
    with mock_client(lambda _: httpx.Response(401, text="test-only-secret SECRET_BODY")) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError) as error:
            model.complete([{"role": "user", "content": "hello"}])
    assert "401" in str(error.value)
    assert "secret" not in str(error.value)
    assert "SECRET" not in str(error.value)
    assert model.metrics["llm_calls"] == 1


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"tool_calls": [{}]}}]}])
def test_malformed_responses_are_sanitized_and_not_retried(body):
    with mock_client(lambda _: httpx.Response(200, json=body)) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError, match="invalid chat-completions"):
            model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 1
    assert model.metrics["estimated_usage"]


def test_retry_cannot_bypass_budget(monkeypatch):
    monkeypatch.setattr("evidenceforge.providers.time.sleep", lambda _: None)
    with mock_client(lambda _: httpx.Response(503)) as transport:
        model = ModelClient(settings(max_tokens=1000), client=transport)
        with pytest.raises(BudgetExceeded):
            model.complete([{"role": "user", "content": "hello"}])
    assert model.metrics["llm_calls"] == 1


def test_truncated_output_is_discarded_and_regenerated_with_larger_allowance():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        body = reply("A broken table |" if len(requests) == 1 else "A complete answer.")
        body["choices"][0]["finish_reason"] = "length" if len(requests) == 1 else "stop"
        body["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING"
        body["usage"]["completion_tokens"] = payload["max_tokens"] if len(requests) == 1 else 80
        body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 20}
        return httpx.Response(200, json=body)

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_output_tokens=1024), client=transport)
        result = model.complete([{"role": "user", "content": "Compare the countries"}], max_output_tokens=2048)
    assert [item["max_tokens"] for item in requests] == [2048, 4096]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert result == {"role": "assistant", "content": "A complete answer.", "tool_calls": []}
    assert "PRIVATE_REASONING" not in str(model.last_response)
    assert model.last_response == {"finish_reason": "stop", "output_limit": 4096, "truncated": False,
                                   "prompt_tokens": 50, "completion_tokens": 80, "reasoning_tokens": 20}
    assert model.metrics["truncated_responses"] == 1
    assert model.metrics["completion_tokens"] == 2128
    assert model.metrics["retries"] == 1


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "max_output_tokens", None])
def test_persistent_truncation_is_an_error_even_when_message_looks_complete(finish_reason):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        body = reply("This sentence looks complete.",
                     usage={"prompt_tokens": 50, "completion_tokens": payload["max_tokens"]})
        if finish_reason is not None:
            body["choices"][0]["finish_reason"] = finish_reason
        return httpx.Response(200, json=body)

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(OutputTruncated, match="No partial answer was accepted"):
            model.complete([{"role": "user", "content": "hello"}])
    assert len(requests) == 2
    assert model.last_response["truncated"]
    assert model.metrics["truncated_responses"] == 2
    assert model.metrics["completion_tokens"] == 4096 + 8192


def test_truncation_retry_requires_budget_for_a_larger_response():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        body = reply("incomplete", usage={"prompt_tokens": 50, "completion_tokens": payload["max_tokens"]})
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    with mock_client(handler) as transport:
        model = ModelClient(settings(max_tokens=7000), client=transport)
        with pytest.raises(BudgetExceeded, match="cannot fund a larger"):
            model.complete([{"role": "user", "content": "hello"}])
    assert len(requests) == 1
    assert model.metrics["completion_tokens"] == 4096


def test_missing_finish_reason_with_reported_usage_below_cap_is_compatible():
    with mock_client(lambda _: httpx.Response(200, json=reply())) as transport:
        model = ModelClient(settings(), client=transport)
        assert model.complete([{"role": "user", "content": "hello"}])["content"] == "A grounded result"
    assert model.last_response["finish_reason"] is None
    assert model.last_response["truncated"] is False
    assert model.metrics["llm_calls"] == 1


def test_explicit_stop_at_output_cap_is_not_assumed_truncated():
    body = reply(usage={"prompt_tokens": 50, "completion_tokens": 256})
    body["choices"][0]["finish_reason"] = "stop"
    with mock_client(lambda _: httpx.Response(200, json=body)) as transport:
        model = ModelClient(settings(), client=transport)
        model.complete([{"role": "user", "content": "hello"}], max_output_tokens=256)
    assert model.metrics["truncated_responses"] == 0


def test_filtered_output_is_an_actionable_error_and_is_not_returned():
    body = reply("PARTIAL_FILTERED_OUTPUT")
    body["choices"][0]["finish_reason"] = "content_filter"
    with mock_client(lambda _: httpx.Response(200, json=body)) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError, match="provider filtered") as error:
            model.complete([{"role": "user", "content": "hello"}])
    assert "PARTIAL_FILTERED_OUTPUT" not in str(error.value)
    assert model.metrics["llm_calls"] == 1
    assert model.metrics["completion_tokens"] == 12


@pytest.mark.parametrize("allowance", [True, 0, 32769, "4096"])
def test_invalid_output_allowance_makes_no_network_request(allowance):
    def handler(_):
        pytest.fail("An invalid allowance must never reach the provider")

    with mock_client(handler) as transport:
        model = ModelClient(settings(), client=transport)
        with pytest.raises(ModelError, match="output allowance"):
            model.complete([{"role": "user", "content": "hello"}], max_output_tokens=allowance)


@pytest.mark.parametrize("content", ['{"ok": true}', '```json\n{"ok": true}\n```', '```\n{"ok":true}\n```'])
def test_parse_json_valid(content):
    assert parse_json(content) == {"ok": True}


@pytest.mark.parametrize("content", ["__import__('os').system('bad')", "prefix {\"ok\":true}", "```python\n{}\n```", "{oops}"])
def test_parse_json_rejects_executable_or_malformed_content(content):
    with pytest.raises(ModelError):
        parse_json(content)
