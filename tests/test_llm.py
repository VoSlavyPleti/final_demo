from llm import close_llm, get_llm


def test_llm_output_limit_and_timeouts(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    model = get_llm()

    assert model.max_tokens == 300_000
    assert model.request_timeout == 1800.0
    assert model.stream_chunk_timeout == 1800.0
    assert model.extra_body["max_tokens"] == 300_000
    assert model.reasoning_effort == "high"
    assert model.extra_body["reasoning_effort"] == "high"
    assert model.http_client is not None
    assert model.http_client._transport._pool._max_keepalive_connections == 0
    close_llm(model)


def test_llm_accepts_attempt_scoped_timeout_and_disables_sdk_retries(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    model = get_llm(request_timeout=42.0, max_retries=0)
    try:
        assert model.request_timeout == 42.0
        assert model.stream_chunk_timeout == 42.0
        assert model.root_client.max_retries == 0
        assert model.http_client.timeout.read == 42.0
    finally:
        close_llm(model)
