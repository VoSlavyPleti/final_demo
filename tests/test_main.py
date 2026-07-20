import inspect
import json
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

import pytest

import llm
import main


def _skill() -> str:
    return (main.SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _reference(name: str) -> str:
    return (main.SKILL_DIR / "references" / name).read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _calibration_case(reference: str, case_id: int) -> str:
    marker = f"CAL-{case_id:02d}"
    match = re.search(
        rf"(?ms)^## {re.escape(marker)}\b.*?(?=^## CAL-|\Z)", reference
    )
    assert match is not None, f"Missing calibration case {marker}"
    return _normalized(match.group(0))


def test_parse_args_accepts_paths() -> None:
    args = main.parse_args(
        ["--contract", "a.txt", "--matrix", "m.json", "--output", "o.json"]
    )
    assert args.contract == Path("a.txt")
    assert args.matrix == Path("m.json")
    assert args.output == Path("o.json")


def test_prepare_workspace_exposes_memory_inputs_and_only_active_skill(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "договор.txt"
    matrix = tmp_path / "матрица.json"
    contract.write_text("1. Условие", encoding="utf-8")
    matrix.write_text("[]", encoding="utf-8")

    workspace, output = main.prepare_workspace(
        contract, matrix, tmp_path / "out" / "result.json"
    )
    try:
        skill_root = workspace / "skills" / "contract-matrix-review"
        assert (skill_root / "SKILL.md").is_file()
        assert (skill_root / "references" / "calibration.md").is_file()
        assert len(list((workspace / "skills").iterdir())) == 1
        assert (workspace / "AGENTS.md").read_text("utf-8") == (
            main.AGENTS_FILE.read_text("utf-8")
        )
        assert (workspace / "inputs" / "contract.txt").read_text("utf-8") == (
            "1. Условие"
        )
        assert (workspace / "inputs" / "matrix.json").read_text("utf-8") == "[]"
        assert (workspace / "outputs" / "working").is_dir()
        assert (workspace / "outputs" / "working" / "subagents").is_dir()
        assert output == (tmp_path / "out" / "result.json").resolve()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_prepare_workspace_rejects_invalid_paths(tmp_path: Path) -> None:
    contract = tmp_path / "contract.docx"
    matrix = tmp_path / "matrix.json"
    contract.write_text("text", encoding="utf-8")
    matrix.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match=".txt"):
        main.prepare_workspace(contract, matrix, tmp_path / "result.json")

    contract = contract.with_suffix(".txt")
    contract.write_text("text", encoding="utf-8")
    with pytest.raises(ValueError, match="Output must be a .json"):
        main.prepare_workspace(contract, matrix, tmp_path / "result.txt")
    with pytest.raises(ValueError, match="must differ"):
        main.prepare_workspace(contract, matrix, matrix)


def test_prompts_are_thin_and_delegate_methodology_to_memory_and_skill() -> None:
    combined = _normalized(main.SYSTEM_PROMPT + "\n" + main.USER_PROMPT)
    assert "`/AGENTS.md`" in combined
    assert "`contract-matrix-review`" in combined
    assert "/inputs/contract.txt" in combined
    assert "/inputs/matrix.json" in combined
    assert "/outputs/result.json" in combined
    assert "самопроверк" in combined
    for stale in (
        "status-adjudication",
        "matrix_groups",
        "extra_contract_findings",
        "uncertainties",
        "candidate_assessments",
        "contract_over_matrix",
    ):
        assert stale not in combined
    assert "не создавай и не объявляй завершённый артефакт" in combined
    assert "/skills/contract-matrix-review/SKILL.md" in combined
    assert "Команды `execute` запускаются из корня workspace" in combined
    assert "соответствующие относительные пути" in combined
    assert "Перед каждым `task` назначь уникальный" in combined
    assert "assigned_matrix_ids" in combined
    assert "короткий заголовок не является заданием" in combined
    assert "Дождись всех задач" in combined
    assert len(main.SYSTEM_PROMPT + main.USER_PROMPT) < 2_200
    assert "�" not in combined


def test_build_agent_uses_memory_skill_and_general_purpose_prompt_fragment(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    def fake_create_deep_agent(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda: "model")

    assert main.build_agent(object()) is not None
    assert len(calls) == 1
    assert calls[0]["skills"] == ["/skills/"]
    assert calls[0]["memory"] == ["/AGENTS.md"]
    assert "tools" not in calls[0]
    assert len(calls[0]["subagents"]) == 1
    subagent = calls[0]["subagents"][0]
    assert subagent["name"] == "general-purpose"
    assert subagent["system_prompt"] == main.SUBAGENT_PROMPT_FRAGMENT
    assert subagent["skills"] == ["/skills/"]
    assert "tools" not in subagent
    assert "model" not in subagent
    assert "interrupt_on" not in calls[0]
    assert "permissions" not in calls[0]


def test_subagent_fragment_combines_static_ownership_with_dynamic_scope() -> None:
    fragment = _normalized(main.SUBAGENT_PROMPT_FRAGMENT)
    assert "делегированный `general-purpose` сабагент" in fragment
    assert "динамическом задании вызывающего агента" in fragment
    assert "/AGENTS.md" in fragment
    assert "/outputs/working/subagents/<scope>.json" in fragment
    assert "/outputs/working/analysis.json" in fragment
    assert "/outputs/result.json" in fragment
    assert "assigned_matrix_ids" in fragment
    assert "groups[].matrix_id" in fragment
    assert re.search(r"точн\w* (совпад|равенств)", fragment)
    assert "остаются неизменными" in fragment
    assert "разделы 2-3" not in fragment


def test_skill_defines_integrated_matrix_first_workflow() -> None:
    normalized = _normalized(_skill())
    assert "references/calibration.md" in normalized
    assert "Сопоставление и статус фиксируются совместно" in normalized
    assert "Для каждой исходной строки матрицы сразу создать одну рабочую matrix-oriented группу" in normalized
    assert "Для каждой применимой matrix-oriented группы" in normalized
    assert "Проверить остаточное содержание договора" in normalized
    assert "Не определять `extra_in_contract` простым вычитанием" in normalized
    assert "Подготовить состав итогового заключения" in normalized
    assert "Проверить полноту и завершить анализ" in normalized
    assert "Делегирование частей анализа" in normalized
    assert "Режим исполнения: проверить до первой записи" in normalized
    assert "это также подтверждается системным сообщением" in normalized
    assert "все упоминания канонических `/outputs/working/analysis.json`, `/outputs/result.json`" in normalized
    assert "В файле отсутствуют `completion_status` и итоговое заключение" in normalized
    assert "Навык применяется в одном из двух режимов" in normalized
    assert "`{\"scope\": \"<scope>\", \"assigned_matrix_ids\": [...], \"groups\": [...]}`" in normalized
    assert "Канонические `/outputs/working/analysis.json` и `/outputs/result.json` ведёт только главный агент" in normalized
    assert "Главный агент дожидается завершения всех назначенных задач" in normalized
    assert "главный агент обязан перепроверить каждую полученную группу" in normalized


def test_delegation_contract_uses_exact_matrix_id_sets_and_scratch_schema() -> None:
    normalized = _normalized(_skill())
    assert "assigned_matrix_ids" in normalized
    assert "groups[].matrix_id" in normalized
    assert "точного равенства мультимножеств" in normalized
    assert "не пропущен" in normalized
    assert "не повторён" in normalized
    for field in (
        "accepted_contract_items",
        "uncovered_or_changed_elements",
        "rejected_weak_candidates",
        "calibration_case_ids",
    ):
        assert field in normalized


def test_skill_makes_candidate_acceptance_independent_from_coverage_status() -> None:
    normalized = _normalized(_skill()).lower()
    assert re.search(r"(сильный|прямой) частичный аналог", normalized)
    assert "техническая возможность" in normalized
    assert "accepted_contract_items" in normalized
    assert "uncovered_or_changed_elements" in normalized
    assert "rejected_weak_candidates" in normalized
    assert re.search(
        r"`missing_in_contract`[^.]{0,500}пуст",
        normalized,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"`deviation`[^.]{0,500}(хотя бы один|непуст)[^.]{0,300}принят",
        normalized,
        flags=re.IGNORECASE,
    )


def test_skill_requires_a_contract_residual_inventory() -> None:
    normalized = _normalized(_skill())
    assert "contract_inventory" in normalized
    assert "disposition" in normalized
    for disposition in (
        "no_residual",
        "residual_attached_to_deviation",
        "extra",
        "non_substantive",
    ):
        assert disposition in normalized
    assert "кажд" in normalized and "смыслов" in normalized
    assert "простым вычитанием" in normalized


def test_skill_anchors_linked_appendices_and_preserves_source_text() -> None:
    normalized = _normalized(_skill()).lower()
    assert "`id: null` оставлен только действительно непривязанному материалу" in normalized
    assert re.search(
        r"`locator`.{0,500}(номерн|якор).{0,500}прилож",
        normalized,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"`text`.{0,500}(полн|дослов).{0,500}прилож",
        normalized,
        flags=re.IGNORECASE,
    )
    assert "дослов" in normalized
    assert "без внесённых многоточ" in normalized
    assert "нормализац" in normalized


def test_skill_defines_status_boundaries_and_final_output() -> None:
    skill = _skill()
    normalized = _normalized(skill)
    for status in (
        "`aligned`",
        "`deviation`",
        "`missing_in_contract`",
        "`optional_absent`",
        "`not_applicable`",
        "`extra_in_contract`",
    ):
        assert status in normalized
    assert "Если часть требования не покрыта, продолжить целевой поиск" in normalized
    assert "Срок, сумма, условие или иное положение договора покрывает элемент матрицы лишь тогда" in normalized
    assert "в `disagreements` присутствуют только `deviation`, `missing_in_contract` и `extra_in_contract`" in normalized
    assert "Поле `calibration_case_ids` используется только для внутренней самопроверки" in normalized
    assert "изменить уже существующий `/outputs/result.json` посредством `edit_file`" in normalized
    assert "Не удалять итоговый файл ради повторного создания" in normalized

    match = re.search(r"```json\s*(\{.*?\})\s*```", skill, flags=re.DOTALL)
    assert match is not None
    example = json.loads(match.group(1))
    assert set(example) == {"completion_status", "disagreements"}
    assert set(example["disagreements"][0]) == {
        "status",
        "matrix_item",
        "contract_items",
        "comment",
    }


def test_calibration_is_anonymous_and_covers_core_boundaries() -> None:
    reference = _reference("calibration.md")
    normalized = _normalized(reference)
    assert len(re.findall(r"(?m)^## CAL-", reference)) == 10
    for case_id in range(1, 11):
        assert f"CAL-{case_id:02d}" in reference
    assert "Граница законодательной ссылки" in normalized
    assert "Одинаковое число, разный триггер" in normalized
    assert "Один договорный пункт может участвовать в разных матричных группах" in normalized
    assert "Настоящий extra" in normalized
    assert "презумпцию применимости" in normalized

    partial_boundary = _calibration_case(reference, 3).lower()
    assert "прям" in partial_boundary and "частич" in partial_boundary
    assert "техническ" in partial_boundary and "возможност" in partial_boundary
    assert "deviation" in partial_boundary
    assert "missing_in_contract" in partial_boundary

    inversion = _calibration_case(reference, 5).lower()
    assert "инверси" in inversion
    assert "юридическ" in inversion and "операц" in inversion
    assert "deviation" in inversion

    residual_extra = _calibration_case(reference, 8).lower()
    assert "остат" in residual_extra
    assert "extra_in_contract" in residual_extra

    optional = _calibration_case(reference, 9).lower()
    assert "optional" in optional
    assert "аналог" in optional
    assert "aligned" in optional and "deviation" in optional

    dependency_isolation = _calibration_case(reference, 10).lower()
    assert "зависим" in dependency_isolation
    assert "самостоятельн" in dependency_isolation
    assert "deviation" in dependency_isolation
    assert "missing_in_contract" in dependency_isolation
    assert not re.search(
        r"(?i)gold|kavkaz|irkutsk|altai|kuzbas|kaluga|\.xlsx|\.txt", reference
    )


def test_skill_has_no_runtime_or_tool_micromanagement() -> None:
    lower = _skill().lower()
    for stale in (
        "до 30 минут",
        "результаты прежних прогонов",
        "powershell",
        "timeout",
        "mapping.json",
        "status-adjudication",
        "matrix_groups",
        "extra_contract_findings",
    ):
        assert stale not in lower


def test_reasoning_defaults_match_current_agent() -> None:
    signature = inspect.signature(llm.get_llm)
    assert signature.parameters["thinking"].default is True
    assert signature.parameters["reasoning_effort"].default == "medium"
    assert signature.parameters["max_completion_tokens"].default == 64_000


def test_run_agent_invokes_one_graph_once(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def invoke(self, payload: object, config: object) -> None:
            captured["payload"] = payload
            captured["config"] = config

    backend = object()
    monkeypatch.setattr(main, "build_backend", lambda workspace: backend)

    def fake_build(received_backend):
        assert received_backend is backend
        captured["build_count"] = int(captured.get("build_count", 0)) + 1
        return FakeAgent()

    monkeypatch.setattr(main, "build_agent", fake_build)
    main.run_agent(tmp_path)
    assert captured["build_count"] == 1
    assert captured["payload"]["messages"][0]["content"] == main.USER_PROMPT
    assert captured["config"]["recursion_limit"] == 10_000


def test_transient_retry_reuses_same_agent_and_thread(
    tmp_path: Path, monkeypatch
) -> None:
    build_count = 0
    invoke_count = 0
    sleeps: list[int] = []
    configs: list[dict] = []

    class FlakyAgent:
        def invoke(self, payload: object, config: object) -> None:
            nonlocal invoke_count
            invoke_count += 1
            configs.append(config)
            if invoke_count == 1:
                raise main.httpx.ReadError("connection reset")

    monkeypatch.setattr(main, "build_backend", lambda workspace: object())

    def fake_build(backend):
        nonlocal build_count
        build_count += 1
        return FlakyAgent()

    monkeypatch.setattr(main, "build_agent", fake_build)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: sleeps.append(seconds))
    main.run_agent(tmp_path)
    assert build_count == 1
    assert invoke_count == 2
    assert sleeps == [2]
    assert configs[0]["configurable"]["thread_id"] == configs[1]["configurable"]["thread_id"]


def test_build_backend_preserves_tools_and_hides_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class FakeBackend:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setattr(main, "WindowsPowerShellBackend", FakeBackend)
    main.build_backend(tmp_path)

    assert captured["root_dir"] == tmp_path
    assert captured["virtual_mode"] is True
    assert captured["max_output_bytes"] == 400_000
    assert captured["inherit_env"] is False
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["DEEPAGENT_WORKSPACE_ROOT"] == str(tmp_path.resolve())
    assert "DEEPSEEK_API_KEY" not in captured["env"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
def test_windows_backend_uses_powershell_and_utf8(tmp_path: Path) -> None:
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        inherit_env=True,
    )
    result = backend.execute("Write-Output 'Привет'")
    assert result.exit_code == 0
    assert "Привет" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
def test_windows_backend_maps_virtual_shell_paths_to_workspace(tmp_path: Path) -> None:
    (tmp_path / "outputs").mkdir()
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        inherit_env=True,
    )
    result = backend.execute(
        "Set-Content -LiteralPath '/outputs/probe.txt' -Value 'ok' -Encoding UTF8"
    )
    assert result.exit_code == 0
    assert (tmp_path / "outputs" / "probe.txt").is_file()
    assert (
        backend._normalize_virtual_shell_paths("Invoke-WebRequest https://host/outputs/x")
        == "Invoke-WebRequest https://host/outputs/x"
    )
    assert (
        backend._normalize_virtual_shell_paths(
            "python -c \"open('/outputs/result.json')\""
        )
        == "python -c \"open('outputs/result.json')\""
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
def test_windows_backend_grep_handles_utf8(tmp_path: Path) -> None:
    sample = tmp_path / "кириллица.txt"
    sample.write_text("первая строка\nточное Привет совпадение\n", encoding="utf-8")
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        inherit_env=True,
    )
    result = backend.grep("Привет", path="/кириллица.txt")
    assert result.error is None
    assert result.matches == [
        {"path": "/кириллица.txt", "line": 2, "text": "точное Привет совпадение"}
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
@pytest.mark.parametrize("requested, expected", [(None, None), (0, None), (7, 7)])
def test_windows_backend_timeout_semantics(
    tmp_path: Path,
    monkeypatch,
    requested: int | None,
    expected: int | None,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        inherit_env=True,
    )
    backend.execute("Write-Output ok", timeout=requested)
    assert captured["timeout"] == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
def test_windows_backend_rejects_negative_timeout(tmp_path: Path) -> None:
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        inherit_env=True,
    )
    with pytest.raises(ValueError, match="non-negative"):
        backend.execute("Write-Output ok", timeout=-1)


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific backend")
def test_windows_backend_returns_nonzero_exit_code(tmp_path: Path) -> None:
    backend = main.WindowsPowerShellBackend(
        root_dir=tmp_path,
        virtual_mode=True,
        inherit_env=True,
    )
    result = backend.execute("Write-Error 'ошибка'; exit 7")
    assert result.exit_code == 7
    assert "Exit code: 7" in result.output


def test_main_publishes_agent_result_and_removes_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "outputs").mkdir(parents=True)
    output = tmp_path / "published" / "result.json"
    output.parent.mkdir(parents=True)

    monkeypatch.setattr(
        main,
        "prepare_workspace",
        lambda contract, matrix, requested: (workspace, output),
    )

    def fake_run(received: Path) -> None:
        assert received == workspace
        (workspace / "outputs" / "result.json").write_text(
            '{"completion_status":"complete","disagreements":[]}',
            encoding="utf-8",
        )

    monkeypatch.setattr(main, "run_agent", fake_run)
    assert (
        main.main(
            ["--contract", "c.txt", "--matrix", "m.json", "--output", "o.json"]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "completion_status": "complete",
        "disagreements": [],
    }
    assert not workspace.exists()
