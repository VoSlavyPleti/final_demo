from llm import get_llm


def test_llm_output_limit_and_timeouts(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    model = get_llm()

    assert model.max_tokens == 300_000
    assert model.request_timeout == 300.0
    assert model.stream_chunk_timeout == 300.0
    assert model.extra_body["max_tokens"] == 300_000
