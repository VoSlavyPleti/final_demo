import json
import os

import httpx
import pytest
from langchain_gigachat import GigaChat

import llm
from llm import GigachatSettings, close_llm, get_llm, set_llm_timeout


@pytest.fixture(autouse=True)
def gateway_environment(monkeypatch):
    # Never use a developer's credentials or certificate configuration in tests.
    for key in os.environ:
        if key.startswith("GIGACHAT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("LOCAL", "false")
    monkeypatch.setenv("GIGACHAT_HOST", "gateway.invalid")
    monkeypatch.setenv("GIGACHAT_PORT", "8080")


def test_llm_uses_example_parameters_and_not_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "old-model")
    monkeypatch.setenv("GIGACHAT_MODEL", "also-not-the-requested-model")
    model = get_llm()
    try:
        assert isinstance(model, GigaChat)
        assert model.model == "glm-5.2"
        assert model._client._settings.model == "glm-5.2"
        assert model.base_url == "http://gateway.invalid:8080/v1"
        assert model.temperature == 0
        assert model.max_tokens == 5120
        assert model.top_p == 0.1
        assert model.repetition_penalty == 1.0
        assert model.reasoning_effort is None
        assert model.streaming is False
        assert model.timeout == 300.0
        assert model._client._client.timeout.read == 300.0
        assert model.max_retries == model._client._settings.max_retries == 0
        assert model.verify_ssl_certs is True
        assert model._client._client.follow_redirects is False
    finally:
        close_llm(model)
    assert model._client._client.is_closed


def test_attempt_timeout_updates_cached_sdk_and_transport() -> None:
    model = get_llm(request_timeout=42.0)
    try:
        assert model._client._client.timeout.read == 42.0
        set_llm_timeout(model, 12.0)
        assert model.timeout == 12.0
        assert model._client._settings.timeout == 12.0
        assert model._client._client.timeout.read == 12.0
    finally:
        close_llm(model)


def test_local_mode_passes_certificate_paths_from_example(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("LOCAL", "true")
    monkeypatch.setenv("GIGACHAT_PORT", "443")
    for variable, name in (
        ("GIGACHAT_TLS_CERT_FILEPATH", "client.pem"),
        ("GIGACHAT_KEY_FILEPATH", "client.key"),
        ("GIGACHAT_CA_BUNDLE_FILEPATH", "ca.pem"),
    ):
        (tmp_path / name).write_text("fixture", encoding="utf-8")
        monkeypatch.setenv(variable, name)

    from unittest.mock import MagicMock

    constructor = MagicMock()
    monkeypatch.setattr(llm, "GigaChat", constructor)
    get_llm()
    params = constructor.call_args.kwargs
    assert params["base_url"] == "https://gateway.invalid:443/v1"
    assert params["cert_file"] == str(tmp_path / "client.pem")
    assert params["key_file"] == str(tmp_path / "client.key")
    assert params["ca_bundle_file"] == str(tmp_path / "ca.pem")
    assert params["verify_ssl_certs"] is True


def test_missing_certificate_fails_before_model_creation(monkeypatch):
    monkeypatch.setenv("LOCAL", "true")
    with pytest.raises(ValueError, match="GIGACHAT_TLS_CERT_FILEPATH"):
        get_llm()


def test_in_cluster_mode_does_not_require_local_certificate_files(monkeypatch):
    monkeypatch.setenv("GIGACHAT_TLS_CERT_FILEPATH", "nonexistent.pem")
    settings = GigachatSettings()
    assert settings.certificate_params() == {}


def test_gateway_settings_can_be_loaded_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("GIGACHAT_HOST")
    monkeypatch.delenv("GIGACHAT_PORT")
    dotenv = tmp_path / "gateway.env"
    dotenv.write_text(
        "GIGACHAT_HOST=company.invalid\nGIGACHAT_PORT=8443\n"
        "GIGACHAT_ENDPOINT=/api/v1\n", encoding="utf-8"
    )
    settings = GigachatSettings(_env_file=dotenv)
    assert settings.base_url == "http://company.invalid:8443/api/v1"


def test_missing_gateway_does_not_fall_back_to_public_provider(monkeypatch):
    monkeypatch.delenv("GIGACHAT_HOST")
    with pytest.raises(ValueError, match="host"):
        get_llm()


def test_close_does_not_create_unused_sdk_client():
    model = GigaChat(model="glm-5.2")
    close_llm(None)
    close_llm(model)
    assert "_client" not in model.__dict__


@pytest.mark.parametrize("delegate", [False, True])
def test_real_gigachat_adapter_completes_harness_tool_round_trip(tmp_path, capsys, delegate):
    import main
    from deepagents.backends import FilesystemBackend

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "glm-5.2"
        assert payload["max_tokens"] == 5120
        assert payload["temperature"] == 0
        assert payload["top_p"] == 0.1
        assert payload["repetition_penalty"] == 1.0
        assert "thinking" not in payload
        assert "reasoning_effort" not in payload
        if len(requests) == 1:
            assert "write_todos" in {f["name"] for f in payload["functions"]}
            message = {
                "role": "assistant", "content": "",
                "function_call": {
                    "name": "task" if delegate else "write_todos",
                    "arguments": (
                        {"description": "Child task", "subagent_type": "general-purpose"}
                        if delegate else
                        {"todos": [{"content": "Check", "status": "completed"}]}
                    ),
                },
            }
            finish_reason = "function_call"
        elif delegate and len(requests) == 2:
            message = {"role": "assistant", "content": "Child complete"}
            finish_reason = "stop"
        else:
            assert any(m["role"] == "function" for m in payload["messages"])
            message = {"role": "assistant", "content": "Done"}
            finish_reason = "stop"
        return httpx.Response(200, json={
            "choices": [{"message": message, "index": 0, "finish_reason": finish_reason}],
            "created": 1, "model": "glm-5.2", "object": "chat.completion",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    model = get_llm()
    close_llm(model)
    model._client._client_instance = httpx.Client(
        base_url=model.base_url, transport=httpx.MockTransport(respond)
    )
    try:
        agent = main.build_agent(
            FilesystemBackend(root_dir=tmp_path, virtual_mode=True), model=model,
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "Check"}]},
            config={
                "configurable": {"thread_id": "glm-test"},
                "callbacks": [main.ConsoleLogHandler()],
            },
        )
        assert result["messages"][-1].content == "Done"
        assert len(requests) == (3 if delegate else 2)
        log = capsys.readouterr().out
        assert log.count("[agent:model_output]") == len(requests)
        assert "[agent:tool_output]" in log
        assert "Done" in log
        if delegate:
            assert "Child complete" in log
    finally:
        close_llm(model)
