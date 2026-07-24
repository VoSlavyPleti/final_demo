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
    assert "main_idea" in prompt
    assert "обязательно прочитай и полностью выполни" in prompt
    assert '"mapping.v1"' in prompt
    assert "ровно один раз" in prompt
    assert "/inputs/contract.txt" in prompt
    assert "/inputs/matrix.json" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "не присваивай юридические статусы" in prompt
    assert "/outputs/result.json" not in prompt


def test_mapping_skill_contains_full_mapping_contract() -> None:
    skill = (main.MAPPING_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.lower().split())
    assert "main_idea" in normalized_skill
    assert "дополнительный поисковый фокус" in normalized_skill
    assert "не ограничивать поиск содержанием `main_idea`" in normalized_skill
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
    assert '"contract_evidence_locator"' in skill
    assert '"unmapped_matrix_ids"' in skill
    assert "Не определять применимость" in skill
    assert "каждый пункт матрицы был классифицирован ровно как" in skill
    assert '"matrix_ids"' not in skill
    assert '"mapped_scope"' not in skill
    assert '"missing_matrix_ids"' not in skill
    assert "/outputs/result.json" not in skill
    assert "/skills/contract-mapping/references/mapping-calibration.md" in skill
    assert "Для такого остаточного аспекта продолжить поиск" in normalized_skill
    assert "Неполнота покрытия не уничтожает сильный mapping" in skill
    assert "Для каждого предварительно отсутствующего ID выполнить одну обратную проверку" in normalized_skill
    assert "один исходный `contract_id` должен встречаться в `mappings` ровно один раз" in normalized_skill
    assert "функционально равнозначные способы исполнения" in normalized_skill
    assert "каждый пункт договора сопоставлять независимо" in skill
    assert "не выполнять отдельный recovery-проход" not in skill.lower()
    assert "Пункты приложений и таблиц не включать в инвентарь" in skill
    assert "Пункты приложений и таблиц не включать в инвентарь и не создавать для них отдельные contract-oriented группы" in normalized_skill
    assert "наличие точного `contract_evidence_locator`" in skill
    assert "Не агрегировать иерархию нумерации" in skill
    assert "наиболее специфичному основному пункту" in normalized_skill
    mapping_calibration = (
        main.MAPPING_SKILL_SOURCE / "references" / "mapping-calibration.md"
    ).read_text(encoding="utf-8")
    assert "Включённый контекст приложения" in mapping_calibration
    assert "Первый полный кандидат не всегда исчерпывает группу" in mapping_calibration
    assert "Специальный механизм против общей темы" in mapping_calibration
    assert "Иерархия нумерации не является одной группой" in mapping_calibration
    assert "Семантически относящийся фрагмент приложения" in mapping_calibration
    assert "Обратный поиск платёжного механизма среди отвлекающих положений" in mapping_calibration


def test_status_prompt_and_skill_define_single_raw_comparison_artifact() -> None:
    prompt = main.STATUS_SUBAGENT_PROMPT.lower()
    skill = (main.STATUS_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.lower().split())
    assert "main_idea" in prompt
    assert "обязательным отдельным входом решения о статусе" in normalized_skill
    assert '"main_idea_assessment"' in skill
    assert '"status_effect"' in skill
    assert "не присваивать candidate status" in normalized_skill
    assert "forces_deviation" in normalized_skill
    assert "supports_aligned" in normalized_skill
    normalized_skill = " ".join(skill.split())
    agents = main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert "contract-group-status" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "/outputs/working/status.json" in prompt
    assert "единственным подробным рабочим контрактом" in prompt
    assert "не назначай приоритет или уровень риска" in prompt
    assert '"mapping.v1"' in prompt
    assert "блокирующий дефект" in " ".join(prompt.split())
    assert "/outputs/working/status-provisional.json" not in prompt
    assert "/outputs/working/mapping-adjustments.json" not in prompt
    assert "# Статус contract-oriented групп" in skill
    assert "Выполнить одну задачу" in skill
    assert "## Обязательный порядок" in skill
    assert "прямого, корреспондирующего или инвертированного" in normalized_skill
    assert "полный mapping заново" in skill
    assert "узкий поиск только этого механизма" in normalized_skill
    assert "внутри единого status-артефакта" in main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert '"schema_version": "status.v7"' in skill
    assert "candidate_assessments" in skill
    assert "contract_evidence" in skill
    assert "operative_elements" in skill
    assert "residual_assessment" in skill
    assert "не создавать `remove`" in normalized_skill.lower()
    assert '"mapping_changes"' in skill
    assert '"differences"' not in skill
    assert "difference_dimensions" not in skill
    assert "status_reason" not in skill
    assert "evaluated_matrix_ids" not in skill
    assert '"dimension"' in skill
    assert "требуемая матрицей сумма, ставка, срок, адресат" in skill
    assert "Два пустых значения не образуют" in skill
    assert "Применимость matrix item определяется один раз" in skill
    assert "данных недостаточно, чтобы уверенно исключить selector" in normalized_skill
    assert "Корреспондирующее право кредитора" in skill
    assert "отсутствие поясняющих слов" in skill.lower()
    assert "ровно одно независимо опровергаемое" in skill
    assert "каждый срок, дату, начало отсчёта, сумму, ставку и формулу" in skill
    assert "Разложить весь исходный contract item" in skill
    assert "не использовать предполагаемую законодательную эквивалентность" in agents
    assert "не устанавливать содержание законов по внешним источникам" in normalized_skill.lower()
    assert "не создают `deviation`" in normalized_skill.lower()
    assert "техническая возможность сами по себе не делают продукт применимым" in normalized_skill
    assert "final_unmapped_matrix_ids" not in skill
    assert all(status in skill for status in (
        "aligned", "deviation", "missing_in_contract", "not_applicable"
    ))
    assert "extra_in_contract" not in skill
    assert "optional_absent" not in skill
    assert "neutral_extra" in skill
    assert "adverse_extra" in skill
    assert "deviation_types" in skill
    calibration = (
        main.STATUS_SKILL_SOURCE / "references" / "calibration.md"
    ).read_text(encoding="utf-8")
    assert "роли после нормализации" in calibration
    assert "placeholder против значения" in calibration
    assert "покрытие найдено в другом пункте" in calibration
    assert "внутреннее включение" in calibration
    assert "приложение и закрытый перечень" in calibration
    assert "разрешённая альтернатива" in calibration
    assert "baseline-кандидат не удаляется" in calibration
    assert "отличие правового эффекта" in calibration
    assert "ссылка на закон не отменяет сравнение" in calibration
    assert "техническая возможность не означает применимость" in calibration
    assert "CAL-24" in calibration
    assert "CAL-25" in calibration
    assert "CAL-26" in calibration
    assert "CAL-27" in calibration
    assert "CAL-28" in calibration
    assert "CAL-29" in calibration
    assert "CAL-30" in calibration
    assert "outputs/working/status.json" in skill
    assert "использовать относительные пути от корня workspace" in normalized_skill
    assert "contract_value" not in calibration
    assert "matrix_value" not in calibration
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
    assert 'schema_version == "mapping.v1"' in prompt
    assert 'completion_status == "complete"' in prompt
    assert "невалидную или незавершённую карту status-агенту не передавать" in normalized_prompt
    assert "после принятия карты вызови subagent `status` для одной задачи" in normalized_prompt
    assert "не вызывай отдельного recovery-агента" in normalized_prompt
    assert "единый `/outputs/working/status.json`" in user_prompt
    assert "после получения валидного артефакта заверши работу" in normalized_prompt
    assert "mapping → status" in user_prompt
    assert "/inputs/contract.txt" in user_prompt
    assert "/inputs/matrix.json" in user_prompt
    assert "/outputs/working/mapping.json" in user_prompt
    assert "/outputs/working/status.json" in user_prompt
    assert "/outputs/working/status-provisional.json" not in user_prompt
    assert "/outputs/working/mapping-adjustments.json" not in user_prompt
    combined = "\n".join((prompt, user_prompt))
    for inactive in ("/outputs/result.json", "mapping-recovered.json"):
        assert inactive not in combined


def test_agents_memory_contains_stable_project_policy() -> None:
    memory = main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert "промежуточную карту юридических аналогов" in memory
    assert "Пункт договора — один исходный нумерованный смысловой пункт основного текста" in memory
    assert "Пункт матрицы — один исходный объект" in memory
    assert "Сопоставление является many-to-many" in memory
    assert "Приложения и таблицы не образуют contract-oriented групп" in memory
    assert "Прямо включённый внутренней ссылкой смысл" in memory
    assert "Каждый нумерованный подпункт образует собственную группу" in memory
    assert "Совпадение только общей темы" in memory
    assert "Mapping-этап не определяет применимость" in memory
    assert "Status-этап принимает карту" in memory
    assert "не выбирает приоритет или необходимость включения пункта" in memory
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
    assert subagents[0]["skills"] == ["/skills/contract-mapping/"]
    assert subagents[1]["skills"] == ["/skills/contract-group-status/"]
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


def test_mapping_validator_rejects_duplicate_contract_ids(tmp_path: Path) -> None:
    artifact = tmp_path / "mapping.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "mapping.v1",
                "completion_status": "complete",
                "mappings": [
                    {"contract_id": "4.4", "contract_locator": "п. 4.4", "candidates": []},
                    {"contract_id": "4.4", "contract_locator": "п. 4.4", "candidates": []},
                ],
                "unmapped_matrix_ids": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="duplicate contract_id: 4.4"):
        main.validate_mapping_artifact(artifact)


def test_status_validator_rejects_in_progress_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "status.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "status.v7",
                "completion_status": "in_progress",
                "groups": [],
                "matrix_review": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="completion_status must be complete"):
        main.validate_status_artifact(artifact)


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
                    "schema_version": "status.v7",
                    "completion_status": "complete",
                    "mapping_changes": [],
                    "contract_profile": {
                        "role_map": {
                            "Банк": ["Банк"],
                            "Предприятие": ["Предприятие"],
                        }
                    },
                    "groups": [
                        {
                            "contract_id": "1.1",
                            "contract_locator": "Основной текст, п. 1.1",
                            "candidates": [
                                {"matrix_id": "2.1", "relation_type": "direct"}
                            ],
                            "candidate_assessments": [
                                {
                                    "matrix_id": "2.1",
                                    "applicability": "applicable",
                                    "status": "aligned",
                                    "deviation_types": [],
                                    "calibration_case_ids": [],
                                    "checked_contract_context": ["п. 1.1"],
                                    "operative_elements": [
                                        {
                                            "element": "оказание услуг",
                                            "dimension": "obligation",
                                            "matrix_quote": "Банк оказывает услуги",
                                            "contract_evidence": [
                                                {
                                                    "contract_id": "1.1",
                                                    "locator": "Основной текст, п. 1.1",
                                                    "quote": "Банк оказывает услуги",
                                                }
                                            ],
                                            "result": "same",
                                            "explanation": "совпадает",
                                        }
                                    ],
                                }
                            ],
                            "residual_assessment": {
                                "status": "none",
                                "remaining_scope": [],
                            },
                            "status": "aligned",
                        }
                    ],
                    "matrix_review": [
                        {
                            "matrix_id": "2.1",
                            "required_type": "mandatory",
                            "applicability": "applicable",
                            "coverage_status": "mapped",
                            "reason": "покрыто",
                        }
                    ],
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
    assert result["schema_version"] == "status.v7"
    assert result["groups"][0]["status"] == "aligned"
    assert sorted(path.name for path in output.parent.iterdir()) == ["result.json"]
