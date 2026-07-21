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


def test_mapping_prompt_delegates_methodology_to_its_skill() -> None:
    prompt = main.MAPPING_SUBAGENT_PROMPT.lower()
    assert "contract-mapping" in prompt
    assert "/inputs/contract.txt" in prompt
    assert "/inputs/matrix.json" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "не присваивай статусы" in prompt
    assert "/outputs/result.json" not in prompt
    assert '"mappings"' not in prompt
    for forbidden in (
        "deviation",
        "missing_in_contract",
        "extra_in_contract",
        "contract-group-status",
    ):
        assert forbidden not in prompt


def test_mapping_skill_defines_mapping_contract_and_calibration() -> None:
    skill = main.MAPPING_SKILL_SOURCE.joinpath("SKILL.md").read_text(
        encoding="utf-8"
    )
    reference = main.MAPPING_SKILL_SOURCE.joinpath(
        "references", "mapping-examples.md"
    ).read_text(encoding="utf-8")

    assert "/outputs/working/mapping.json" in skill
    assert "many-to-many" in skill
    assert "contract-oriented" in skill
    assert '"mappings"' in skill
    assert '"mapped_scope"' in skill
    assert '"missing_matrix_ids"' in skill
    assert "не определять применимость" in skill
    assert "не присваивать статусы" in skill
    assert "fallback" in skill
    assert "прямая связь имеет приоритет перед инверсией" in skill
    assert "объединение mapped и missing id" in skill.lower()
    assert "MAP-05" in reference
    assert "инверсия как fallback" in reference
    assert "если прямого договорного положения" in reference.casefold()
    assert "не добавлять c-e" in reference.lower()
    for forbidden in ("gold", "KAVKAZ", "Кавказ", ".xlsx", "4.4", "4.2.12"):
        assert forbidden.casefold() not in reference.casefold()


def test_status_prompt_uses_fixed_mapping_and_its_skill() -> None:
    prompt = main.STATUS_SUBAGENT_PROMPT.lower()
    assert "contract-group-status" in prompt
    assert "/outputs/working/mapping.json" in prompt
    assert "/outputs/working/status.json" in prompt
    assert "не меняй mapping" in prompt
    assert "не формируй итоговое заключение" in prompt


def test_orchestrator_prompt_and_skill_define_stage_order() -> None:
    prompt = main.ORCHESTRATOR_SYSTEM_PROMPT.lower()
    skill = (main.ORCHESTRATOR_SKILL_SOURCE / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "agents.md" in prompt
    assert "contract-review-orchestration" in prompt
    assert "subagent `mapping`" in prompt
    assert "subagent `status`" in prompt
    assert skill.index("## 1. Mapping") < skill.index("## 2. Status")
    assert skill.index("## 2. Status") < skill.index("## 3. Conclusion")
    assert "/outputs/working/mapping.json" in skill
    assert "/outputs/working/status.json" in skill
    assert "/outputs/result.json" in skill
    assert "только пункты из `evaluated_matrix_ids`" in skill
    assert all(
        status in skill
        for status in ("deviation", "missing_in_contract", "extra_in_contract")
    )


def test_status_skill_defines_applicability_and_status_artifact() -> None:
    skill = (main.STATUS_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    reference = (
        main.STATUS_SKILL_SOURCE / "references" / "calibration.md"
    ).read_text(encoding="utf-8")
    assert "/outputs/working/status.json" in skill
    assert "mapping неизменяемым" in skill
    assert all(
        selector in skill
        for selector in (
            "only_for_lot",
            "only_for_product",
            "payment_method",
            "only_for_terminal",
        )
    )
    assert '"matrix_review"' in skill
    assert '"evaluated_matrix_ids"' in skill
    assert "references/calibration.md" in skill
    assert "Калибровочные примеры" in reference
    for forbidden in ("gold", "KAVKAZ", "Кавказ", ".xlsx"):
        assert forbidden.casefold() not in reference.casefold()


def test_agents_memory_contains_stable_project_policy() -> None:
    memory = main.AGENT_MEMORY_SOURCE.read_text(encoding="utf-8")
    assert "предварительный протокол разногласий" in memory
    assert "Банковская матрица — эталон" in memory
    assert all(
        status in memory
        for status in ("deviation", "missing_in_contract", "extra_in_contract")
    )
    assert "Приложения" in memory
    assert "/outputs/" not in memory
    assert "mapping → status" not in memory


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
        orchestration = (
            workspace
            / "skills"
            / "orchestrator"
            / "contract-review-orchestration"
            / "SKILL.md"
        )
        status = (
            workspace
            / "skills"
            / "status"
            / "contract-group-status"
            / "SKILL.md"
        )
        mapping = (
            workspace
            / "skills"
            / "mapping"
            / "contract-mapping"
            / "SKILL.md"
        )
        mapping_examples = mapping.parent / "references" / "mapping-examples.md"
        calibration = status.parent / "references" / "calibration.md"
        assert orchestration.is_file()
        assert mapping.is_file()
        assert mapping_examples.is_file()
        assert status.is_file()
        assert calibration.is_file()
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


def test_build_agent_registers_two_specialists(
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
    assert captured["name"] == "contract-review-orchestrator"
    assert captured["model"] is model
    assert captured["backend"] is backend
    assert captured["memory"] == ["/AGENTS.md"]
    assert captured["skills"] == ["/skills/orchestrator/"]
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert [subagent["name"] for subagent in subagents] == ["mapping", "status"]
    assert subagents[0]["system_prompt"] == main.MAPPING_SUBAGENT_PROMPT
    assert subagents[0]["skills"] == ["/skills/mapping/"]
    assert subagents[1]["system_prompt"] == main.STATUS_SUBAGENT_PROMPT
    assert subagents[1]["skills"] == ["/skills/status/"]


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
        "open('/outputs/result.json'); open('/skills/status/SKILL.md')\""
    )
    assert main.WindowsPowerShellBackend._normalize_virtual_shell_paths(command) == (
        "python -c \"open('inputs/contract.txt'); "
        "open('outputs/result.json'); open('skills/status/SKILL.md')\""
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


def test_main_publishes_only_final_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"

    def fake_run_agent(workspace: Path) -> None:
        (workspace / "outputs" / "working" / "mapping.json").write_text(
            '{"completion_status":"complete"}', encoding="utf-8"
        )
        (workspace / "outputs" / "working" / "status.json").write_text(
            '{"completion_status":"complete"}', encoding="utf-8"
        )
        (workspace / "outputs" / "result.json").write_text(
            json.dumps(
                {
                    "completion_status": "complete",
                    "disagreements": [
                        {
                            "status": "deviation",
                            "contract_items": [],
                            "matrix_items": [],
                            "comment": "test",
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
    assert result["disagreements"][0]["status"] == "deviation"
    assert sorted(path.name for path in output.parent.iterdir()) == ["result.json"]
