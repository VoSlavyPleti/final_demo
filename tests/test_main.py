from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

import main


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    contract = tmp_path / "договор.txt"
    matrix = tmp_path / "матрица.json"
    contract.write_text("1.1 Банк оказывает услуги.", encoding="utf-8")
    matrix.write_text(
        json.dumps(
            [
                {
                    "number": "2.1",
                    "text": "Банк оказывает услуги.",
                    "required_type": "mandatory",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return contract, matrix


def test_mapping_prompt_delegates_method_to_its_skill() -> None:
    prompt = main.MAPPING_SUBAGENT_PROMPT.lower()
    assert "subagent `mapping`" in prompt
    assert "contract-mapping" in prompt
    assert "обязательно прочитай и полностью выполни" in prompt
    assert "/inputs/contract.txt" in prompt
    assert "/inputs/matrix.json" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "не присваивай юридические статусы" in prompt
    assert "/outputs/result.json" not in prompt


def test_mapping_skill_contains_full_mapping_contract() -> None:
    skill = (main.MAPPING_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    assert "contract-oriented" in skill
    assert "many-to-many" in skill
    assert '"mappings"' in skill
    assert '"schema_version": "mapping.v1"' in skill
    assert '"candidates"' in skill
    assert '"relation_type"' in skill
    assert '"shared_legal_relation"' in skill
    assert '"contract_evidence"' in skill
    assert '"matrix_evidence"' in skill
    assert '"unmapped_matrix_ids"' in skill
    assert "Не определять применимость" in skill
    assert "каждый пункт матрицы был классифицирован ровно как" in skill
    assert '"matrix_ids"' not in skill
    assert '"mapped_scope"' not in skill
    assert '"missing_matrix_ids"' not in skill
    assert "/outputs/result.json" not in skill
    assert "/skills/contract-mapping/references/mapping-calibration.md" in skill
    assert "продолжить поиск только для иных самостоятельных аспектов" in normalized_skill
    assert "Не завершать инвентарь на основном тексте договора" in skill
    assert "каждый пункт договора сопоставлять независимо" in skill
    assert "не выполнять отдельный recovery-проход" not in skill.lower()


def test_mapping_recovery_prompt_and_skill_are_targeted() -> None:
    prompt = main.RECOVERY_SUBAGENT_PROMPT.lower()
    skill = (main.RECOVERY_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert "contract-mapping-recovery" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "/outputs/working/mapping-recovered.json" in prompt
    assert "не выполняй полный mapping заново" in prompt
    assert "не присваивай статусы" in prompt
    assert "группы с пустым `candidates`" in skill
    assert "срокам, датам" in skill
    assert "корреспондирующие права и обязанности" in skill
    assert "Не проверять заново полностью согласованные группы" in skill
    assert 'схемы `mapping.v1`' in skill


def test_orchestrator_contract_is_mapping_only() -> None:
    prompt = main.ORCHESTRATOR_SYSTEM_PROMPT.lower()
    normalized_prompt = " ".join(prompt.split())
    user_prompt = main.RUN_PROMPT.lower()
    assert "agents.md" in prompt
    assert "subagent `mapping`" in prompt
    assert "первым содержательным действием вызови subagent `mapping`" in prompt
    assert "не выполняй юридическое сопоставление самостоятельно" in prompt
    assert "дождись завершения mapper" in prompt
    assert "после принятия базовой карты один раз вызови subagent `mapping-recovery`" in normalized_prompt
    assert "другие этапы анализа не запускай" in normalized_prompt
    assert "mapping и точечный mapping-recovery" in user_prompt
    assert "/inputs/contract.txt" in user_prompt
    assert "/inputs/matrix.json" in user_prompt
    assert "/outputs/working/mapping.json" in user_prompt
    assert "/outputs/working/mapping-recovered.json" in user_prompt
    combined = "\n".join((prompt, user_prompt))
    for inactive in (
        "contract-group-status",
        "/outputs/working/status.json",
        "/outputs/result.json",
        "missing_in_contract",
        "extra_in_contract",
    ):
        assert inactive not in combined


def test_agents_memory_contains_stable_project_policy() -> None:
    memory = main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert "промежуточную карту юридических аналогов" in memory
    assert "Пункт договора — один исходный нумерованный пункт" in memory
    assert "Пункт матрицы — один исходный объект" in memory
    assert "Сопоставление является many-to-many" in memory
    assert "Совпадение только общей темы" in memory
    assert "не определять применимость требований" in memory
    assert "/outputs/" not in memory
    for inactive in (
        "deviation",
        "missing_in_contract",
        "extra_in_contract",
        "протокол разногласий",
    ):
        assert inactive not in memory


def test_prepare_workspace_installs_memory_skills_and_inputs(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    workspace, output = main.prepare_workspace(
        contract, matrix, tmp_path / "published" / "result.json"
    )
    try:
        assert output == (tmp_path / "published" / "result.json").resolve()
        assert (workspace / "AGENTS.md").read_bytes() == main.AGENT_MEMORY_SOURCE.read_bytes()
        assert (workspace / "inputs" / "contract.txt").read_bytes() == contract.read_bytes()
        assert (workspace / "inputs" / "matrix.json").read_bytes() == matrix.read_bytes()
        assert (workspace / "outputs" / "working").is_dir()
        mapping = workspace / "skills" / "contract-mapping" / "SKILL.md"
        calibration = mapping.parent / "references" / "mapping-calibration.md"
        recovery = workspace / "skills" / "contract-mapping-recovery" / "SKILL.md"
        assert mapping.is_file()
        assert calibration.is_file()
        assert recovery.is_file()
        assert not (workspace / "skills" / "contract-mapping-orchestration").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.parametrize(
    ("contract_name", "matrix_name", "output_name", "message"),
    [
        ("contract.docx", "matrix.json", "result.json", "Contract must be a .txt"),
        ("contract.txt", "matrix.txt", "result.json", "Matrix must be a .json"),
        ("contract.txt", "matrix.json", "result.txt", "Output must be a .json"),
    ],
)
def test_prepare_workspace_validates_extensions(
    tmp_path: Path,
    contract_name: str,
    matrix_name: str,
    output_name: str,
    message: str,
) -> None:
    contract = tmp_path / contract_name
    matrix = tmp_path / matrix_name
    contract.write_text("x", encoding="utf-8")
    matrix.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        main.prepare_workspace(contract, matrix, tmp_path / output_name)


def test_build_agent_registers_mapping_and_recovery_specialists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    registrations: list[tuple[str, object]] = []

    class FakeModel:
        model_name = "deepseek-v4-pro"

    model = FakeModel()

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda: model)
    monkeypatch.setattr(
        main,
        "register_harness_profile",
        lambda key, profile: registrations.append((key, profile)),
    )
    backend = main.build_backend(tmp_path)

    main.build_agent(backend)

    assert registrations[0][0] == "openai:deepseek-v4-pro"
    profile = registrations[0][1]
    assert profile.general_purpose_subagent.enabled is False
    assert captured["name"] == "contract-mapping-orchestrator"
    assert captured["model"] is model
    assert captured["backend"] is backend
    assert captured["memory"] == ["/AGENTS.md"]
    assert "skills" not in captured
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert [subagent["name"] for subagent in subagents] == [
        "mapping",
        "mapping-recovery",
    ]
    assert all(subagent["skills"] == ["/skills/"] for subagent in subagents)
    assert subagents[0]["system_prompt"] == main.MAPPING_SUBAGENT_PROMPT
    assert subagents[1]["system_prompt"] == main.RECOVERY_SUBAGENT_PROMPT


def test_compact_trace_handler_omits_payloads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    handler = main.CompactTraceHandler()
    model_run = uuid.uuid4()
    tool_run = uuid.uuid4()
    handler.on_chat_model_start({}, [["secret prompt"]], run_id=model_run)
    handler.on_llm_end(None, run_id=model_run)
    handler.on_tool_start({"name": "read_file"}, "secret input", run_id=tool_run)
    handler.on_tool_end("secret output", run_id=tool_run)
    trace = capsys.readouterr().out
    assert all(event in trace for event in ("model_start", "model_end", "tool_start", "tool_end"))
    assert "read_file" in trace
    assert "secret" not in trace


def test_windows_virtual_path_normalization() -> None:
    command = (
        "python -c \"open('/inputs/contract.txt'); "
        "open('/outputs/working/mapping.json'); open('/skills/contract-mapping/SKILL.md')\""
    )
    assert main.WindowsPowerShellBackend._normalize_virtual_shell_paths(command) == (
        "python -c \"open('inputs/contract.txt'); "
        "open('outputs/working/mapping.json'); open('skills/contract-mapping/SKILL.md')\""
    )


@pytest.mark.skipif(main.os.name != "nt", reason="Windows backend test")
def test_windows_backend_execute_preserves_utf8_and_cwd(tmp_path: Path) -> None:
    response = main.build_backend(tmp_path).execute("Write-Output 'Привет'; (Get-Location).Path")
    assert response.exit_code == 0
    assert "Привет" in response.output
    assert str(tmp_path.resolve()).lower() in response.output.lower()


@pytest.mark.skipif(main.os.name != "nt", reason="Windows backend test")
def test_windows_backend_reports_nonzero_exit(tmp_path: Path) -> None:
    response = main.build_backend(tmp_path).execute(
        "[Console]::Error.WriteLine('ошибка'); exit 7"
    )
    assert response.exit_code == 7
    assert "ошибка" in response.output
    assert "Exit code: 7" in response.output


def test_main_publishes_only_recovered_mapping_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"

    def fake_run_agent(workspace: Path) -> None:
        payload = json.dumps(
            {
                "completion_status": "complete",
                "schema_version": "mapping.v1",
                "mappings": [
                    {
                        "contract_id": "1.1",
                        "contract_locator": "Основной текст, п. 1.1",
                        "candidates": [
                            {
                                "matrix_id": "2.1",
                                "relation_type": "direct",
                                "shared_legal_relation": "оказание услуг",
                                "contract_evidence": "Банк оказывает услуги",
                                "matrix_evidence": "Банк оказывает услуги",
                            }
                        ],
                    }
                ],
                "unmapped_matrix_ids": [],
            },
            ensure_ascii=False,
        )
        (workspace / "outputs" / "working" / "mapping.json").write_text(
            payload,
            encoding="utf-8",
        )
        (workspace / "outputs" / "working" / "mapping-recovered.json").write_text(
            payload,
            encoding="utf-8",
        )

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    exit_code = main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["completion_status"] == "complete"
    assert result["mappings"][0]["contract_id"] == "1.1"
    assert sorted(path.name for path in output.parent.iterdir()) == ["result.json"]
