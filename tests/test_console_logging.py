import json
from concurrent.futures import ThreadPoolExecutor

from gigachat.exceptions import ResponseError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from console_logging import ConsoleLogHandler
from main import CompactTraceHandler


def test_console_shows_intermediate_messages_and_tool_activity(capsys):
    handler = ConsoleLogHandler()
    handler.on_chat_model_start({}, [[HumanMessage(content="Проверь договор")]], run_id="model")
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="Сначала прочитаю документ", tool_calls=[{
            "name": "read_file", "args": {"file_path": "/inputs/contract.txt"}, "id": "call",
        }],
    ))]]), run_id="model")
    handler.on_tool_start({"name": "read_file"}, "inputs/contract.txt", run_id="tool", parent_run_id="model")
    handler.on_tool_end(ToolMessage(content="1.1. Текст договора", tool_call_id="call"), run_id="tool", parent_run_id="model")
    output = capsys.readouterr().out
    for text in ("Проверь договор", "Сначала прочитаю документ", "read_file", "1.1. Текст договора", "parent=model"):
        assert text in output
    assert output.index("model_output") < output.index("tool_start") < output.index("tool_output")


def test_console_does_not_pollute_metadata_trace(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    for handler in (CompactTraceHandler(path), ConsoleLogHandler()):
        handler.on_tool_start({"name": "execute"}, "print('Договор')", run_id="tool")
        handler.on_tool_end("Договор stdout\n[stderr] Диагностика", run_id="tool")
    assert "Договор stdout" in capsys.readouterr().out
    assert "Договор" not in path.read_text(encoding="utf-8")


def test_console_redacts_secrets_and_does_not_dump_client_config(monkeypatch, capsys):
    secret = 'secret-with-"quotes"-and-\\slashes'
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", secret)
    handler = ConsoleLogHandler()
    handler.on_chat_model_start({"password": "hidden-client-config"}, [[
        HumanMessage(content=f"{secret} Bearer new-session-token"),
    ]], run_id="model")
    handler.on_tool_end("-----BEGIN PRIVATE KEY-----\nkey material\n-----END PRIVATE KEY-----", run_id="tool")
    handler.on_llm_error(ResponseError(
        "https://example.invalid", 403, b"private body", {"Authorization": "private header"}
    ), run_id="model")
    output = capsys.readouterr().out
    for forbidden in (secret, json.dumps(secret)[1:-1], "new-session-token", "key material", "hidden-client-config", "private body", "private header"):
        assert forbidden not in output
    assert "403" in output
    assert "[REDACTED]" in output


def test_parallel_callback_records_are_not_interleaved(capsys):
    handler = ConsoleLogHandler()
    def emit(index):
        handler.on_tool_end(f"begin-{index}\nend-{index}", run_id=str(index), parent_run_id="parent")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(emit, range(20)))
    output = capsys.readouterr().out
    assert output.count("[agent:tool_output]") == 20
    for index in range(20):
        assert f"begin-{index}\nend-{index}" in output
