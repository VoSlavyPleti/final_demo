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


def test_status_prompt_and_skill_integrate_targeted_recovery() -> None:
    prompt = main.STATUS_SUBAGENT_PROMPT.lower()
    skill = (main.STATUS_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert "contract-group-status" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "/outputs/working/status-provisional.json" in prompt
    assert "/outputs/working/mapping-adjustments.json" in prompt
    assert "/outputs/working/status.json" in prompt
    assert "не выполняй полный mapping заново" in prompt
    assert "локальным восстановлением mapping" in skill
    assert "корреспондирующее право и обязанность" in skill
    assert "применимый обязательный пункт матрицы остался без кандидата" in skill
    assert "обрабатывать только элементы `recovery_queue`" in skill.lower()
    assert "не запускать recovery только из-за отличия срока" in skill.lower()
    assert "baseline-набору плюс" in skill
    assert "нет `extra_in_contract` с кандидатами" in skill
    assert '"schema_version": "status.v2"' in skill
    assert '"mapping_changes"' in skill
    assert '"difference_basis"' in skill
    assert "final_unmapped_matrix_ids" not in skill
    assert all(status in skill for status in (
        "aligned", "deviation", "extra_in_contract", "missing_in_contract"
    ))
    calibration = (
        main.STATUS_SKILL_SOURCE / "references" / "calibration.md"
    ).read_text(encoding="utf-8")
    assert "корреспондирующие способы оплаты" in calibration
    assert "документы приёмки" in calibration
    assert "инверсия ролей" in calibration
    assert "placeholder" in calibration
    assert "альтернативы матрицы" in calibration
    assert "неприменимый кандидат" in calibration
    assert "законная конкретизация" in calibration
    assert "исправление слабого baseline-кандидата" in calibration
    for forbidden in ("gold", "KAVKAZ", "Кавказ", ".xlsx"):
        assert forbidden.casefold() not in calibration.casefold()


def test_orchestrator_contract_runs_mapping_then_status() -> None:
    prompt = main.ORCHESTRATOR_SYSTEM_PROMPT.lower()
    normalized_prompt = " ".join(prompt.split())
    user_prompt = main.RUN_PROMPT.lower()
    assert "agents.md" in prompt
    assert "subagent `mapping`" in prompt
    assert "первым содержательным действием вызови subagent `mapping`" in prompt
    assert "не выполняй юридическое сопоставление самостоятельно" in prompt
    assert "дождись завершения mapper" in prompt
    assert "после принятия карты один раз вызови subagent `status`" in normalized_prompt
    assert "не вызывай отдельного recovery-агента" in normalized_prompt
    assert "baseline плюс зарегистрированные `add`" in normalized_prompt
    assert "mapping → status" in user_prompt
    assert "/inputs/contract.txt" in user_prompt
    assert "/inputs/matrix.json" in user_prompt
    assert "/outputs/working/mapping.json" in user_prompt
    assert "/outputs/working/status-provisional.json" in user_prompt
    assert "/outputs/working/mapping-adjustments.json" in user_prompt
    assert "/outputs/working/status.json" in user_prompt
    combined = "\n".join((prompt, user_prompt))
    for inactive in ("/outputs/result.json", "mapping-recovered.json"):
        assert inactive not in combined


def test_agents_memory_contains_stable_project_policy() -> None:
    memory = main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert "промежуточную карту юридических аналогов" in memory
    assert "Пункт договора — один исходный нумерованный пункт" in memory
    assert "Пункт матрицы — один исходный объект" in memory
    assert "Сопоставление является many-to-many" in memory
    assert "Совпадение только общей темы" in memory
    assert "Mapping-этап не определяет применимость" in memory
    assert "Status-этап принимает карту" in memory
    assert "/outputs/" not in memory
    assert "Полный mapping заново не выполняется" in memory


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
        status = workspace / "skills" / "contract-group-status" / "SKILL.md"
        status_calibration = status.parent / "references" / "calibration.md"
        assert mapping.is_file()
        assert calibration.is_file()
        assert status.is_file()
        assert status_calibration.is_file()
        assert not (workspace / "skills" / "contract-mapping-recovery").exists()
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


def test_build_agent_registers_mapping_and_status_specialists(
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
    assert captured["name"] == "contract-analysis-orchestrator"
    assert captured["model"] is model
    assert captured["backend"] is backend
    assert captured["memory"] == ["/AGENTS.md"]
    assert "skills" not in captured
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert [subagent["name"] for subagent in subagents] == [
        "mapping",
        "status",
    ]
    assert all(subagent["skills"] == ["/skills/"] for subagent in subagents)
    assert subagents[0]["system_prompt"] == main.MAPPING_SUBAGENT_PROMPT
    assert subagents[1]["system_prompt"] == main.STATUS_SUBAGENT_PROMPT


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


def test_main_publishes_only_status_artifact(
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
        (workspace / "outputs" / "working" / "status.json").write_text(
            json.dumps(
                {
                    "schema_version": "status.v2",
                    "completion_status": "complete",
                    "mapping_changes": [],
                    "contract_profile": {},
                    "groups": [
                        {
                            "contract_id": "1.1",
                            "contract_locator": "Основной текст, п. 1.1",
                            "candidates": [],
                            "evaluated_matrix_ids": [],
                            "status": "extra_in_contract",
                            "comment": "",
                            "difference_basis": None,
                            "source_kind": "main_body",
                            "independent_legal_obligation": True,
                        }
                    ],
                    "matrix_review": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workspace / "outputs" / "working" / "mapping-adjustments.json").write_text(
            json.dumps(
                {"completion_status": "complete", "changes": []},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workspace / "outputs" / "working" / "status-provisional.json").write_text(
            json.dumps(
                {
                    "completion_status": "complete",
                    "groups": [],
                    "recovery_queue": [],
                },
                ensure_ascii=False,
            ),
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
    assert result["schema_version"] == "status.v2"
    assert result["groups"][0]["status"] == "extra_in_contract"
    assert sorted(path.name for path in output.parent.iterdir()) == ["result.json"]
