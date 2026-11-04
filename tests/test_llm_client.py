import json
from dataclasses import replace

import httpx
import pytest

from integrations.llm import LLMClient, LLMError, LLMSettings


def settings(**changes):
    return replace(
        LLMSettings(
            enabled=True,
            api_key="secret-test-key",
            base_url="https://provider.example/v1",
            model="chosen-model",
        ),
        **changes,
    )


def response(**changes):
    return {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 1,
        "model": "served-model",
        "usage": {"total_tokens": 12},
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"reason":"ok"}',
                    **changes,
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "base_url,path,extra",
    [
        ("https://api.deepseek.com", "/chat/completions", {"thinking": {"type": "disabled"}}),
        (
            "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/",
            "/compatible-mode/v1/chat/completions",
            {"enable_thinking": False},
        ),
        ("http://127.0.0.1:11434/v1", "/v1/chat/completions", {}),
    ],
)
def test_sdk_uses_configured_provider_bearer_and_json_contract(base_url, path, extra):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == path
        assert request.headers["Authorization"] == "Bearer secret-test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "chosen-model"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["stream"] is False and payload["max_tokens"] == 1200
        assert all(payload[k] == v for k, v in extra.items())
        assert "JSON Schema" in payload["messages"][0]["content"]
        assert '"required"' in payload["messages"][0]["content"]
        assert "_llm_configuration" not in request.content.decode()
        return httpx.Response(200, json=response())

    client = LLMClient(
        settings(base_url=base_url, extra_body_json=json.dumps(extra)),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.generate(
            system="Return JSON",
            context={"facts": {}, "_llm_configuration": "private"},
            schema={"type": "object", "required": ["reason"]},
        )
        assert result["model"] == "served-model" and result["usage"]["total_tokens"] == 12
        assert len(requests) == 1  # No OAuth or hidden retry request.
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["json_schema", "text"])
def test_alternative_response_modes_and_token_parameter(mode):
    def handler(request):
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1200 and "max_tokens" not in body
        if mode == "text":
            assert "response_format" not in body
        else:
            assert body["response_format"] == {
                "type": "json_schema",
                "json_schema": {
                    "name": "inspection_recommendation",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            }
        return httpx.Response(200, json=response())

    client = LLMClient(
        settings(json_mode=mode, token_limit_field="max_completion_tokens"),
        transport=httpx.MockTransport(handler),
    )
    try:
        client.generate(system="Return JSON", context={}, schema={"type": "object"})
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "BAD_REQUEST"),
        (401, "AUTH_FAILED"),
        (402, "QUOTA_EXHAUSTED"),
        (403, "ACCESS_DENIED"),
        (404, "MODEL_NOT_FOUND"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
        (302, "UPSTREAM_ERROR"),
    ],
)
def test_provider_errors_are_redacted_and_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://other.example/secret-test-key"},
            json={"error": {"message": "PRIVATE_BODY secret-test-key"}},
        )

    client = LLMClient(settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMError) as error:
            client.generate(system="JSON", context={}, schema={})
        assert error.value.code == code
        assert "secret-test-key" not in str(error.value)
        assert "PRIVATE_BODY" not in str(error.value)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout, "TIMEOUT"),
        (httpx.ConnectError, "CONNECTION_ERROR"),
    ],
)
def test_network_errors_are_safe(exception, code):
    def handler(request):
        raise exception("secret-test-key", request=request)

    client = LLMClient(settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMError) as error:
            client.models()
        assert error.value.code == code and "secret-test-key" not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": None},
        response(content="   "),
        response(content=None),
        response(refusal="Not available"),
        response(
            tool_calls=[
                {"id": "call1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ]
        ),
        {**response(), "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    ],
)
def test_incomplete_refused_and_truncated_responses_are_rejected(body):
    client = LLMClient(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(LLMError, match="неполный|некорректный"):
            client.generate(system="JSON", context={}, schema={})
    finally:
        client.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://external.example/v1"},
        {"base_url": "https://user:secret@example.com/v1"},
        {"base_url": "https://example.com/v1?api_key=secret"},
        {"base_url": "https://example.com/v1#secret"},
        {"base_url": "https://example.com:bad/v1"},
        {"json_mode": "unknown"},
        {"token_limit_field": "wrong"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"max_tokens": 0},
        {"extra_body_json": "[]"},
        {"extra_body_json": "bad"},
        {"extra_body_json": '{"messages":[]}'},
        {"extra_body_json": '{"stream":true}'},
    ],
)
def test_invalid_configuration_rejected_before_http(changes):
    config = settings(**changes)
    assert not config.public_status(False)["network_allowed"]
    with pytest.raises(LLMError) as error:
        LLMClient(config)
    assert error.value.code == "CONFIGURATION_ERROR"


def test_models_check_and_status_do_not_expose_endpoint_or_key():
    def handler(request):
        assert request.url.path == "/v1/models" and request.method == "GET"
        return httpx.Response(200, json={"data": [{"id": "chosen-model"}]})

    config = settings()
    assert "secret-test-key" not in repr(config)
    status = config.public_status(False)
    assert status["network_allowed"] and status["protocol"] == "openai_compatible"
    assert "api_key" not in status and "base_url" not in status
    assert not config.public_status(True)["network_allowed"]
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        assert client.models() == ["chosen-model"]
    finally:
        client.close()


def test_environment_loads_only_explicit_generic_configuration(monkeypatch):
    values = {
        "LLM_ENABLED": "1",
        "LLM_API_KEY": " secret-test-key ",
        "LLM_BASE_URL": "https://provider.example/v1/",
        "LLM_MODEL": "chosen-model",
        "LLM_JSON_MODE": "text",
        "LLM_MAX_TOKENS": "2048",
        "LLM_TIMEOUT_SECONDS": "30",
        "LLM_AUTO_ANALYZE": "0",
        "LLM_EXTRA_BODY_JSON": '{"enable_thinking":false}',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = LLMSettings.from_env()
    assert config.configured and config.api_key == "secret-test-key"
    assert config.base_url == "https://provider.example/v1" and not config.auto_analyze
    assert config.max_tokens == 2048 and config.timeout_seconds == 30
    assert config.extra_body() == {"enable_thinking": False}


def test_cli_does_not_make_requests_when_disabled(monkeypatch, capsys):
    from api.config import Settings
    from integrations.llm import __main__ as cli

    monkeypatch.setattr(
        cli.Settings, "from_env", lambda: Settings(storage_dir=None, llm=settings(enabled=False))
    )
    monkeypatch.setattr(cli, "LLMClient", lambda _: pytest.fail("Network client was created"))
    monkeypatch.setattr("sys.argv", ["llm", "--check"])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
