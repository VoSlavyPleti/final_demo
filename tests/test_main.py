from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
import uuid

import httpx
import pytest
from deepagents.middleware.skills import _list_skills_with_errors

import main


def _result_payload() -> dict:
    return {
        "schema_version": "contract-matrix-map.v6",
        "contract_items": [
            {
                "contract_id": "1.1",
                "matrix_ids": ["2.1"],
                "status": "aligned",
                "comment": "Обязанность оказать услугу совпадает.",
            },
            {
                "contract_id": "1.2",
                "matrix_ids": [],
                "status": "extra_in_contract",
                "comment": (
                    "Добавлена не предусмотренная матрицей обязанность "
                    "Предприятия передавать ежемесячный отчёт."
                ),
            },
        ],
        "matrix_items": [
            {
                "matrix_id": "3.1",
                "status": "missing_in_contract",
                "comment": (
                    "Mandatory-требование применимо; обязанность получить "
                    "согласия работников отсутствует во всём договоре."
                ),
            },
        ],
    }


def _write_result(workspace: Path, payload: dict | None = None) -> None:
    result = workspace / main.RESULT_ARTIFACT
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(
        json.dumps(payload or _result_payload(), ensure_ascii=False),
        encoding="utf-8",
    )


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    contract = tmp_path / "договор.txt"
    matrix = tmp_path / "матрица.json"
    contract.write_text("1.1. Условие.", encoding="utf-8")
    matrix.write_text(
        json.dumps([{"matrix_id": "2.1"}], ensure_ascii=False),
        encoding="utf-8",
    )
    return contract, matrix


def test_prepare_workspace_mounts_one_domain_skill(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    workspace, output, source_contract, source_matrix = main.prepare_workspace(
        contract,
        matrix,
        tmp_path / "result.json",
    )
    try:
        assert output == (tmp_path / "result.json").resolve()
        assert source_contract == contract.resolve()
        assert source_matrix == matrix.resolve()
        assert (workspace / "inputs" / "contract.txt").read_text(
            encoding="utf-8"
        ) == "1.1. Условие."
        mounted = workspace / "skills" / "contract-matrix-review"
        assert (mounted / "SKILL.md").is_file()
        assert (mounted / "references" / "business-deviation-policy.md").is_file()
        assert not (mounted / "references" / "business-target-filter.md").exists()
        assert (mounted / "references" / "business-casebook.md").is_file()
        assert (mounted / "references" / "output-schema.md").is_file()
        assert not (mounted / "scripts").exists()
        assert len(list((workspace / "skills").iterdir())) == 1
        assert (workspace / ".harness_runtime" / "sitecustomize.py").is_file()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_prepare_workspace_validates_paths(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    wrong_contract = contract.with_suffix(".docx")
    wrong_contract.write_text("not txt", encoding="utf-8")
    with pytest.raises(ValueError):
        main.prepare_workspace(wrong_contract, matrix, tmp_path / "x.json")
    with pytest.raises(ValueError):
        main.prepare_workspace(contract, matrix, tmp_path / "x.txt")
    with pytest.raises(FileNotFoundError):
        main.prepare_workspace(tmp_path / "missing.txt", matrix, tmp_path / "x.json")


@pytest.mark.parametrize(
    ("contract_bytes", "matrix_text", "message"),
    [
        (b"\xff", '[{"matrix_id":"1"}]', "valid UTF-8"),
        (b"1. Term", "not-json", "valid JSON"),
        (b"1. Term", "{}", "non-empty array"),
        (b"1. Term", "[]", "non-empty array"),
        (b"1. Term", "[1]", "must be an object"),
    ],
)
def test_prepare_workspace_rejects_invalid_sources_before_creating_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    contract_bytes: bytes,
    matrix_text: str,
    message: str,
) -> None:
    contract = tmp_path / "contract.txt"
    matrix = tmp_path / "matrix.json"
    contract.write_bytes(contract_bytes)
    matrix.write_text(matrix_text, encoding="utf-8")
    created = False

    def must_not_create(*args, **kwargs):
        nonlocal created
        del args, kwargs
        created = True
        raise AssertionError("workspace must not be created")

    monkeypatch.setattr(main.tempfile, "mkdtemp", must_not_create)
    with pytest.raises(ValueError, match=message):
        main.prepare_workspace(contract, matrix, tmp_path / "result.json")
    assert created is False


def test_invalid_backend_environment_cannot_fall_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BACKEND", "typo")
    with pytest.raises(SystemExit):
        main.parse_args(
            [
                "--contract",
                "contract.txt",
                "--matrix",
                "matrix.json",
                "--output",
                "result.json",
            ]
        )


def test_prepare_workspace_rejects_derived_artifact_aliasing_input(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract.txt"
    matrix = tmp_path / "result.manifest.json"
    contract.write_text("1. Условие", encoding="utf-8")
    matrix.write_text('[{"matrix_id":"1"}]', encoding="utf-8")

    with pytest.raises(ValueError, match="manifest paths"):
        main.prepare_workspace(contract, matrix, tmp_path / "result.json")


def test_build_agent_uses_harness_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda **kwargs: object())
    backend = main.build_backend(tmp_path)
    main.build_agent(backend, checkpointer=object())

    assert captured["system_prompt"] == main.AGENT_SYSTEM_PROMPT
    assert captured["skills"] == ["/skills/"]
    assert "subagents" not in captured
    assert "tools" not in captured


def test_skills_parent_path_discovers_domain_skill(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    workspace, *_ = main.prepare_workspace(
        contract,
        matrix,
        tmp_path / "result.json",
    )
    try:
        backend = main.build_backend(workspace)
        skills, error = _list_skills_with_errors(backend, "/skills/")
        wrong_level, wrong_error = _list_skills_with_errors(
            backend,
            "/skills/contract-matrix-review/",
        )

        assert error is None
        assert [item["name"] for item in skills] == ["contract-matrix-review"]
        assert wrong_error is None
        assert wrong_level == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_prompts_only_define_context_and_deliverable() -> None:
    system = main.AGENT_SYSTEM_PROMPT.lower()
    user = main.RUN_PROMPT.lower()
    combined = "\n".join((system, user))

    assert "/inputs/contract.txt" not in system
    assert "/inputs/matrix.json" not in system
    assert "/outputs/result.json" not in system
    assert "/inputs/contract.txt" in user
    assert "/inputs/matrix.json" in user
    assert "/skills/contract-matrix-review/" in user
    assert "/outputs/result.json" in user
    assert "единственным источником бизнес-правил" in system
    assert "самостоятельно организуй" in system
    assert "все исходные пункты договора" in user
    assert "повторяющиеся номера не объединены" in user
    assert "однозначный `source_locator`" in user
    assert "ровно одна запись и один итоговый статус" in user
    assert "одну полную проверку охвата" in user
    assert "отсутствие точки не позволяет" in user
    assert not (main.PROMPTS_ROOT / "quality-repair-user.md").exists()

    for forbidden in (
        "write_todos",
        "subagent",
        "status-audit",
        "coverage-audit",
        "missing-sweep",
        "adversarial",
        "рабочий процесс",
        "построй полную первичную карту",
        "одним итоговым проходом",
        "повторяй проверку",
        "general-purpose",
        "deviation",
        "extra_in_contract",
        "missing_in_contract",
    ):
        assert forbidden not in combined


def test_trace_persists_metadata_without_payload_text(tmp_path: Path) -> None:
    trace = tmp_path / main.TRACE_ARTIFACT
    handler = main.CompactTraceHandler(trace)
    run_id = uuid.uuid4()
    secret = "СЕКРЕТНЫЙ ТЕКСТ ДОГОВОРА"

    handler.on_tool_start(
        {"name": "read_file"},
        '{"file_path":"/inputs/contract.txt","note":"' + secret + '"}',
        run_id=run_id,
    )
    handler.on_tool_end(secret, run_id=run_id)

    raw_trace = trace.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_trace.splitlines()]
    assert secret not in raw_trace
    assert events[0]["paths"] == ["inputs/contract.txt"]
    assert events[0]["input_size_bytes"] > 0
    assert len(events[0]["input_sha256"]) == 64
    assert events[1]["output_size_bytes"] > 0
    assert len(events[1]["output_sha256"]) == 64
    assert main.CompactTraceHandler._safe_paths(
        '{"file_path":"tmp/mapping-stage-a.json"}'
    ) == ["tmp/mapping-stage-a.json"]
    assert main.CompactTraceHandler._safe_paths(
        "Проверить outputs/result.json и перечитать результат"
    ) == ["outputs/result.json"]


def test_skill_separates_mapping_policy_and_target_casebook() -> None:
    skill = main.DOMAIN_SKILL_SOURCE / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    policy = (
        skill.parent / "references" / "business-deviation-policy.md"
    ).read_text(encoding="utf-8")
    schema = (
        skill.parent / "references" / "output-schema.md"
    ).read_text(encoding="utf-8")
    casebook = (
        skill.parent / "references" / "business-casebook.md"
    ).read_text(encoding="utf-8")
    combined = "\n".join((text, policy, casebook, schema))
    policy_compact = " ".join(policy.lower().split())

    assert all(
        path.suffix.lower() == ".md"
        for path in main.DOMAIN_SKILL_SOURCE.rglob("*")
        if path.is_file()
    )

    assert text.startswith("---\n")
    assert "description: \"" in text
    assert "эквайринга" in text
    assert "задаёт только метод анализа" in policy
    assert "общая тема без такой связи не образует mapping" in policy.lower()
    assert "обрезать формальным лимитом" in policy.lower()
    assert "остаточный статус" in policy.lower()
    assert "совпадает с casebook по правоотношению" in policy.lower()
    assert "placeholder в действующем условии" in casebook.lower()
    assert "всегда образует c01" in casebook.lower()
    assert "не создавай для той же строки одновременно deviation и missing" in policy_compact
    assert "отсутствующего внешнего текста" in policy.lower()
    assert "не является основанием добавить" in policy_compact
    assert "внутреннее противоречие не является основанием" in policy_compact
    assert "право одной стороны потребовать действие" in policy.lower()
    assert "подписываемое заверение" in text.lower()
    assert "main_idea" in text
    assert "не создаёт и не расширяет требование" in text
    assert "main_idea" not in policy
    assert "main_idea" not in casebook
    assert "source_locator" not in policy
    assert "source_locator" not in casebook
    assert "порядок выбора статуса" in policy.lower()
    assert "не подбирай строку только ради устранения" in policy_compact
    assert "если смысловых аналогов нет" in policy_compact
    assert "сначала ищи наиболее точный аналог" in policy_compact
    assert "может быть частичным аналогом" in policy_compact
    assert "приоритетные исключения" not in combined.lower()
    assert "raw_delta" not in combined.lower()
    assert "raw-deltas.json" not in combined.lower()
    assert "business-target-filter" not in combined.lower()

    assert '"contract_items"' in schema
    assert '"matrix_items"' in schema
    assert '"matrix_ids"' in schema
    assert '"status"' in schema
    assert '"comment"' in schema
    assert "contract-matrix-map.v6" in schema
    assert '"source_locator"' in schema
    assert "не создавай синтетический номер" in schema
    assert '"contract_text"' not in schema
    assert '"matrix_text"' not in schema
    assert '"review_items"' not in schema
    assert '"completion_status"' not in schema

    for forbidden in (
        "write_todos",
        "subagent",
        "не более трёх",
        "рабочий процесс",
        "status-audit",
        "coverage-audit",
        "business_category",
    ):
        assert forbidden not in combined


def test_casebook_is_independent_and_has_no_gold_anchors() -> None:
    skill_root = main.DOMAIN_SKILL_SOURCE
    casebook = skill_root / "references" / "business-casebook.md"
    target_filter = skill_root / "references" / "business-target-filter.md"
    casebook_text = casebook.read_text(encoding="utf-8").lower()
    combined = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (skill_root / "SKILL.md", *skill_root.rglob("*.md"))
    )

    assert casebook.is_file()
    assert not target_filter.exists()
    assert "business-casebook" in combined
    assert casebook_text.count("\n## c") == 21
    assert "контрольные примеры границы статусов" in casebook_text
    assert "ту же строку матрицы не дублировать как `missing_in_contract`" in casebook_text
    assert "совпадающий пункт — `aligned`, противоречащий — `deviation`" in casebook_text
    assert "пример:" not in casebook_text
    assert "gold" not in casebook_text
    assert "benchmark" not in casebook_text
    for leaked_reference in (
        "kaluga",
        "kuzbas",
        "irkutsk",
        "kavkaz",
        "altai",
    ):
        assert leaked_reference not in casebook_text


def test_windows_backend_maps_virtual_paths_and_utf8(tmp_path: Path) -> None:
    backend = main.build_backend(tmp_path)
    result = backend.execute(
        "New-Item -ItemType Directory -Force /outputs | Out-Null; "
        "Set-Content -LiteralPath /outputs/кириллица.txt -Value 'тест' "
        "-Encoding utf8; Get-Content -LiteralPath /outputs/кириллица.txt "
        "-Encoding utf8"
    )
    assert result.exit_code == 0
    assert "тест" in result.output
    assert (tmp_path / "outputs" / "кириллица.txt").is_file()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("/outputs/result.json", "outputs/result.json"),
        ("C:/outputs/result.json", "outputs/result.json"),
        (r"D:\inputs\contract.txt", r"inputs\contract.txt"),
        ("/skills/review/SKILL.md", "skills/review/SKILL.md"),
    ],
)
def test_windows_backend_normalizes_workspace_aliases(
    source: str, expected: str
) -> None:
    assert (
        main.WindowsPowerShellBackend._normalize_virtual_shell_paths(source)
        == expected
    )


def test_windows_backend_does_not_rewrite_unrelated_paths() -> None:
    command = (
        "Get-Content C:/temp/outputs/result.json; "
        "Invoke-WebRequest https://example.test/outputs/result.json"
    )
    assert (
        main.WindowsPowerShellBackend._normalize_virtual_shell_paths(command)
        == command
    )


def test_python_script_maps_drive_rooted_output_to_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "outputs").mkdir()
    filename = f"path-regression-{tmp_path.name}.txt"
    physical_alias = Path("C:/outputs") / filename
    assert not physical_alias.exists()

    script = tmp_path / "review_result.py"
    script.write_text(
        "from pathlib import Path\n"
        f"target = Path('C:/outputs/nested/{filename}')\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "assert target.parent.exists()\n"
        "target.write_text('reviewed', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = main.build_backend(tmp_path).execute("python review_result.py")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "outputs" / "nested" / filename).read_text(
        encoding="utf-8"
    ) == "reviewed"
    assert not physical_alias.exists()


def test_python_script_maps_virtual_open_to_workspace(tmp_path: Path) -> None:
    (tmp_path / "outputs").mkdir()
    script = tmp_path / "write_result.py"
    script.write_text(
        "with open('/outputs/result.txt', 'w', encoding='utf-8') as stream:\n"
        "    stream.write('workspace')\n",
        encoding="utf-8",
    )

    result = main.build_backend(tmp_path).execute("python write_result.py")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "outputs" / "result.txt").read_text(
        encoding="utf-8"
    ) == "workspace"


def test_python_reviewer_updates_workspace_result_not_drive_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "outputs").mkdir()
    filename = f"review-{tmp_path.name}.json"
    workspace_result = tmp_path / "outputs" / filename
    workspace_result.write_text('{"status": "draft"}', encoding="utf-8")
    physical_alias = Path("C:/outputs") / filename
    assert not physical_alias.exists()

    script = tmp_path / "fix_result.py"
    script.write_text(
        "import json\n"
        f"with open('C:/outputs/{filename}', 'r', encoding='utf-8') as stream:\n"
        "    data = json.load(stream)\n"
        "data['status'] = 'reviewed'\n"
        f"with open('C:/outputs/{filename}', 'w', encoding='utf-8') as stream:\n"
        "    json.dump(data, stream)\n",
        encoding="utf-8",
    )

    result = main.build_backend(tmp_path).execute("python fix_result.py")

    assert result.exit_code == 0, result.output
    assert json.loads(workspace_result.read_text(encoding="utf-8")) == {
        "status": "reviewed"
    }
    assert not physical_alias.exists()


def test_quality_gate_accepts_minimal_result(tmp_path: Path) -> None:
    _write_result(tmp_path)
    assert main.quality_gate_failures(tmp_path) == []


def test_quality_gate_rejects_legacy_fields_and_invalid_items(
    tmp_path: Path,
) -> None:
    payload = _result_payload()
    payload["completion_status"] = "complete"
    payload["contract_items"][0]["contract_text"] = "Лишний исходный текст"
    payload["matrix_items"][0]["matrix_id"] = "2.1"
    _write_result(tmp_path, payload)

    failures = main.quality_gate_failures(tmp_path)
    assert any("unsupported top-level keys: completion_status" in item for item in failures)
    assert any("unsupported keys: contract_text" in item for item in failures)
    assert any("both mapped and missing_in_contract: 2.1" in item for item in failures)


def test_quality_gate_allows_duplicate_source_ids_with_unique_locators(
    tmp_path: Path,
) -> None:
    payload = _result_payload()
    payload["contract_items"][1]["contract_id"] = "1.1"
    payload["contract_items"][0]["source_locator"] = "первое вхождение"
    payload["contract_items"][1]["source_locator"] = "второе вхождение"
    _write_result(tmp_path, payload)
    assert main.quality_gate_failures(tmp_path) == []


def test_quality_gate_requires_unique_locators_for_duplicate_source_ids(
    tmp_path: Path,
) -> None:
    payload = _result_payload()
    payload["contract_items"][1]["contract_id"] = "1.1"
    _write_result(tmp_path, payload)
    failures = main.quality_gate_failures(tmp_path)
    assert any("requires source_locator for every occurrence" in item for item in failures)

    payload["contract_items"][0]["source_locator"] = "одно место"
    payload["contract_items"][1]["source_locator"] = "одно место"
    _write_result(tmp_path, payload)
    failures = main.quality_gate_failures(tmp_path)
    assert any("requires unique source_locator values" in item for item in failures)


def test_quality_gate_checks_status_specific_matrix_ids(tmp_path: Path) -> None:
    payload = _result_payload()
    payload["contract_items"][0]["matrix_ids"] = []
    payload["contract_items"][1]["status"] = "not_applicable"
    payload["contract_items"][1]["matrix_ids"] = ["4.1"]
    payload["contract_items"].append(
        {
            "contract_id": "1.3",
            "matrix_ids": ["4.2"],
            "status": "extra_in_contract",
            "comment": "Самостоятельное условие.",
        }
    )
    _write_result(tmp_path, payload)

    failures = main.quality_gate_failures(tmp_path)
    assert any("aligned requires at least one matrix_id" in item for item in failures)
    assert any("not_applicable requires empty matrix_ids" in item for item in failures)
    assert any("extra_in_contract requires empty matrix_ids" in item for item in failures)


def test_quality_gate_rejects_multiple_statuses_without_crashing(
    tmp_path: Path,
) -> None:
    payload = _result_payload()
    payload["contract_items"][0]["status"] = ["aligned", "deviation"]
    payload["matrix_items"][0]["status"] = ["missing_in_contract", "needs_review"]
    _write_result(tmp_path, payload)

    failures = main.quality_gate_failures(tmp_path)
    assert any("contract_items[0] has unsupported status" in item for item in failures)
    assert any("matrix_items[0] has unsupported status" in item for item in failures)


def test_run_agent_retries_transient_failure_in_same_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, dict]] = []

        def invoke(self, payload, config):
            self.calls.append((payload, config))
            if len(self.calls) == 1:
                request = httpx.Request("POST", "https://example.invalid")
                response = httpx.Response(429, request=request)
                raise main.openai.RateLimitError(
                    "retry", response=response, body=None
                )
            _write_result(tmp_path)

    fake = FakeAgent()
    monkeypatch.setattr(main, "build_backend", lambda workspace: object())
    monkeypatch.setattr(main, "build_agent", lambda backend, checkpointer: fake)
    main.run_agent(
        tmp_path,
        max_retries=1,
        thread_id="stable",
        sleep=lambda _: None,
    )
    assert len(fake.calls) == 2
    assert all(
        call[1]["configurable"]["thread_id"] == "stable" for call in fake.calls
    )
    assert main.RUN_PROMPT in fake.calls[0][0]["messages"][0]["content"]
    assert main.RUN_PROMPT in fake.calls[1][0]["messages"][0]["content"]


def test_run_agent_fails_invalid_result_without_model_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, dict]] = []

        def invoke(self, payload, config):
            self.calls.append((payload, config))
            invalid = _result_payload()
            invalid["schema_version"] = "contract-matrix-map.v5"
            _write_result(tmp_path, invalid)

    fake = FakeAgent()
    monkeypatch.setattr(main, "build_backend", lambda workspace: object())
    monkeypatch.setattr(main, "build_agent", lambda backend, checkpointer: fake)
    with pytest.raises(RuntimeError, match="structural validation"):
        main.run_agent(
            tmp_path,
            max_retries=1,
            thread_id="stable",
            sleep=lambda _: None,
        )

    assert len(fake.calls) == 1
    assert fake.calls[0][1]["configurable"]["thread_id"] == "stable"


def test_aef_attempt_loss_is_not_treated_as_model_retry() -> None:
    from aef_workstation import AefAttemptLost

    class LostAgent:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, payload, config):
            del payload, config
            self.calls += 1
            raise AefAttemptLost("ambiguous execute")

    agent = LostAgent()
    with pytest.raises(AefAttemptLost):
        main._invoke_with_transient_retries(
            agent,
            "task",
            {},
            max_retries=3,
            sleep=lambda _: None,
        )
    assert agent.calls == 1


def test_attempt_deadline_bounds_model_transport_and_checks_stream_tokens() -> None:
    from aef_workstation import AefAttemptLost

    closed = threading.Event()

    class Client:
        timeout = None

        def close(self) -> None:
            closed.set()

    class Model:
        request_timeout = 1800.0
        stream_chunk_timeout = 1800.0
        http_client = Client()
        root_client = Client()

    deadline = time.monotonic() + 12.0

    def check() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AefAttemptLost("safe deadline reached")
        return remaining

    model = Model()
    handler = main.AttemptDeadlineHandler(check, model=model)
    handler.on_chat_model_start(run_id="first")
    assert 11.5 < model.request_timeout < 12.0
    assert model.stream_chunk_timeout == model.request_timeout
    assert model.http_client.timeout.read == model.request_timeout
    assert model.root_client.timeout.read == model.request_timeout

    # The heartbeat may shorten expiresAt while a stream emits no chunks.
    # The active watchdog must observe the new deadline, not the initial one.
    deadline = time.monotonic() + 0.05
    assert closed.wait(timeout=0.5)

    deadline = time.monotonic() - 1
    with pytest.raises(AefAttemptLost, match="deadline"):
        handler.on_llm_new_token("chunk")


def test_aef_result_is_verified_before_local_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aef_workstation

    events: list[str] = []
    reports_out: list[dict] = []

    class Manager:
        def ensure_healthy(self) -> None:
            events.append("health")

        def verify_integrity(self) -> None:
            events.append("integrity")

        def download_result(self) -> bytes:
            events.append("download")
            return json.dumps(_result_payload(), ensure_ascii=False).encode()

    class Supervisor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self.reports: list[dict] = []

        def run(self, workspace: Path, callback):
            del workspace
            manager = Manager()
            value = callback(manager, object(), "thread", 1)
            manager.ensure_healthy()
            manager.verify_integrity()
            self.reports.append({"attempt_no": 1, "status": "complete"})
            return value

    monkeypatch.setattr(aef_workstation, "RunSupervisor", Supervisor)
    monkeypatch.setattr(main, "get_llm", lambda **kwargs: object())
    monkeypatch.setattr(main, "close_llm", lambda model: None)
    monkeypatch.setattr(
        main,
        "_invoke_agent",
        lambda *args, **kwargs: events.append("invoke"),
    )

    def gate(workspace: Path) -> list[str]:
        assert (workspace / main.RESULT_ARTIFACT).is_file()
        events.append("gate")
        return []

    monkeypatch.setattr(main, "quality_gate_failures", gate)
    thread_id, reports = main.run_agent_aef(
        tmp_path,
        settings=object(),
        run_id="run",
        max_infra_restarts=0,
        attempt_reports_out=reports_out,
    )

    assert thread_id == "thread"
    assert reports == reports_out == [{"attempt_no": 1, "status": "complete"}]
    assert events == [
        "health",
        "invoke",
        "health",
        "integrity",
        "download",
        "health",
        "integrity",
        "gate",
        "health",
        "integrity",
    ]


def test_aef_failure_exposes_attempt_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aef_workstation

    reports_out: list[dict] = []

    class Supervisor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self.reports = [
                {
                    "attempt_no": 1,
                    "status": "failed",
                    "restart_reason": "RuntimeError",
                }
            ]

        def run(self, workspace: Path, callback):
            del workspace, callback
            raise RuntimeError("gate failed")

    monkeypatch.setattr(aef_workstation, "RunSupervisor", Supervisor)
    monkeypatch.setattr(main, "close_llm", lambda model: None)
    with pytest.raises(RuntimeError, match="gate failed"):
        main.run_agent_aef(
            tmp_path,
            settings=object(),
            run_id="run",
            max_infra_restarts=0,
            attempt_reports_out=reports_out,
        )
    assert reports_out == [
        {
            "attempt_no": 1,
            "status": "failed",
            "restart_reason": "RuntimeError",
        }
    ]


def test_atomic_write_keeps_old_target_and_removes_temp_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "result.json"
    target.write_bytes(b"old")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise PermissionError("publication blocked")

    monkeypatch.setattr(main.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="publication blocked"):
        main._atomic_write_bytes(b"new", target)

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".result.json.*.tmp")) == []


def test_main_publishes_single_agent_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    seen: dict[str, Path] = {}

    def fake_run(workspace: Path, **kwargs) -> None:
        seen["workspace"] = workspace
        _write_result(workspace)
        trace = workspace / main.TRACE_ARTIFACT
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text('{"event":"test"}\n', encoding="utf-8")

    monkeypatch.setattr(main, "run_agent", fake_run)
    code = main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
            "--backend",
            "local",
        ]
    )
    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == _result_payload()
    assert output.with_name("result.trace.jsonl").is_file()
    manifest = json.loads(
        output.with_name("result.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "contract-review-run.v2"
    assert manifest["status"] == "complete"
    assert manifest["backend"] == "local"
    assert manifest["gate_status"] == "passed"
    assert manifest["publication_status"] == "complete"
    assert manifest["result_sha256"] == main._sha256(output)
    staging = {
        entry["virtual_path"]: entry for entry in manifest["staging_entries"]
    }
    assert staging["/inputs/contract.txt"] == {
        "virtual_path": "/inputs/contract.txt",
        "bytes": len("1.1. Условие.".encode("utf-8")),
        "sha256": main._sha256(contract),
    }
    assert "/inputs/matrix.json" in staging
    assert "/skills/contract-matrix-review/SKILL.md" in staging
    assert "/.harness_runtime/sitecustomize.py" in staging
    assert not seen["workspace"].exists()


def test_manifest_reuses_snapshot_of_files_actually_mounted_before_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    real_prepare = main.prepare_workspace
    expected: dict[str, object] = {}

    def prepare_with_distinct_mounted_skill(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        workspace = prepared[0]
        skill = workspace / "skills" / "contract-matrix-review" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "\n<!-- mounted snapshot -->\n",
            encoding="utf-8",
        )
        mounted_snapshot = main._capture_staging_snapshot(workspace)
        expected["contract_sha256"] = mounted_snapshot.contract_sha256
        expected["matrix_sha256"] = mounted_snapshot.matrix_sha256
        expected["skill_sha256"] = mounted_snapshot.skill_sha256
        expected["runtime_sha256"] = mounted_snapshot.runtime_sha256
        expected["skill_entry_sha256"] = main._sha256(skill)
        return prepared

    def fake_run(workspace: Path, **kwargs) -> None:
        del kwargs
        contract.write_text("source changed after staging", encoding="utf-8")
        matrix.write_text('[{"matrix_id":"changed"}]', encoding="utf-8")
        (workspace / "inputs" / "contract.txt").write_text(
            "mounted input changed after snapshot",
            encoding="utf-8",
        )
        (workspace / "skills" / "contract-matrix-review" / "SKILL.md").write_text(
            "mounted skill changed after snapshot",
            encoding="utf-8",
        )
        (workspace / ".harness_runtime" / "sitecustomize.py").write_text(
            "# mounted runtime changed after snapshot\n",
            encoding="utf-8",
        )
        _write_result(workspace)
        trace = workspace / main.TRACE_ARTIFACT
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text('{"event":"test"}\n', encoding="utf-8")

    monkeypatch.setattr(main, "prepare_workspace", prepare_with_distinct_mounted_skill)
    monkeypatch.setattr(main, "run_agent", fake_run)

    assert main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
            "--backend",
            "local",
        ]
    ) == 0

    manifest = json.loads(
        output.with_name("result.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["contract_sha256"] == expected["contract_sha256"]
    assert manifest["matrix_sha256"] == expected["matrix_sha256"]
    assert manifest["skill_sha256"] == expected["skill_sha256"]
    assert manifest["runtime_sha256"] == expected["runtime_sha256"]
    staging = {
        entry["virtual_path"]: entry for entry in manifest["staging_entries"]
    }
    assert (
        staging["/skills/contract-matrix-review/SKILL.md"]["sha256"]
        == expected["skill_entry_sha256"]
    )


def test_failed_run_publishes_trace_then_manifest_but_not_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    trace_output = output.with_name("result.trace.jsonl")
    manifest_output = output.with_name("result.manifest.json")
    publication_order: list[str] = []
    real_atomic_copy = main._atomic_copy
    real_atomic_write_json = main._atomic_write_json

    def fake_run(workspace: Path, **kwargs) -> None:
        del kwargs
        _write_result(workspace)
        trace = workspace / main.TRACE_ARTIFACT
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text('{"event":"safe.failure"}\n', encoding="utf-8")
        raise RuntimeError("agent failed")

    def observed_copy(source: Path, target: Path) -> None:
        if target == trace_output:
            publication_order.append("trace")
        real_atomic_copy(source, target)

    def observed_write_json(payload: dict, target: Path) -> None:
        if target == manifest_output:
            publication_order.append("manifest")
        real_atomic_write_json(payload, target)

    monkeypatch.setattr(main, "run_agent", fake_run)
    monkeypatch.setattr(main, "_atomic_copy", observed_copy)
    monkeypatch.setattr(main, "_atomic_write_json", observed_write_json)

    assert main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
            "--backend",
            "local",
        ]
    ) == 1

    assert not output.exists()
    assert trace_output.read_text(encoding="utf-8") == '{"event":"safe.failure"}\n'
    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["gate_status"] == "failed"
    assert manifest["publication_status"] == "diagnostics_published"
    assert manifest["error"] == "RuntimeError"
    assert publication_order == ["trace", "manifest"]


def test_failed_run_publishes_empty_trace_when_failure_precedes_tracing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"

    def fail_before_trace(workspace: Path, **kwargs) -> None:
        del workspace, kwargs
        raise RuntimeError("failed before trace handler")

    monkeypatch.setattr(main, "run_agent", fail_before_trace)
    assert main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
            "--backend",
            "local",
        ]
    ) == 1

    trace = output.with_name("result.trace.jsonl")
    manifest = output.with_name("result.manifest.json")
    assert trace.is_file() and trace.read_bytes() == b""
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["publication_status"] == "diagnostics_published"
    assert payload["trace_sha256"] == hashlib.sha256(b"").hexdigest()
    assert payload["result_sha256"] is None


def test_aef_failure_never_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aef_workstation import AefInfrastructureError

    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    seen: dict[str, object] = {"local": False}

    def fail_aef(workspace: Path, **kwargs):
        seen["workspace"] = workspace
        raise AefInfrastructureError("corporate DNS/VPN/IFT connectivity required")

    def local_must_not_run(*args, **kwargs):
        del args, kwargs
        seen["local"] = True

    monkeypatch.setattr(main, "run_agent_aef", fail_aef)
    monkeypatch.setattr(main, "run_agent", local_must_not_run)

    code = main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
            "--backend",
            "aef",
        ]
    )

    assert code == 1
    assert seen["local"] is False
    assert not output.exists()
    shutil.rmtree(seen["workspace"], ignore_errors=True)
