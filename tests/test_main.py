from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
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


def test_analysis_prompt_runs_analyzer_then_final_reviewer() -> None:
    prompt = main.ANALYSIS_SYSTEM_PROMPT.lower()
    assert "оркестратор полного сравнения договора" in prompt
    assert "integrated-contract-analysis" in prompt
    assert "final-finding-review" in prompt
    assert "analyzer" in prompt
    assert "final-reviewer" in prompt
    assert "не дели" in prompt
    assert "`analysis.v3`" in prompt
    assert "/inputs/contract.txt" in prompt
    assert "/inputs/matrix.json" in prompt
    assert "/outputs/working/fragments/full-analysis.json" in prompt
    assert "/outputs/working/analysis.json" in prompt
    assert "/outputs/working/final-result.json" in prompt
    assert "/outputs/result.json" not in prompt
    assert "contract-oriented" not in prompt
    assert "many-to-many" not in prompt


def test_integrated_skill_defines_mapping_and_status_in_one_artifact() -> None:
    skill = (main.ANALYSIS_SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.lower().split())
    assert "# Единый анализ договора по матрице" in skill
    assert "по пути, назначенному задачей" in normalized_skill
    assert "одном непрерывном контексте" in normalized_skill
    assert '"schema_version": "analysis.v3"' in skill
    assert "одновременно проверить покрытие и достроить группу" in normalized_skill
    assert (
        "при отсутствии такого кандидата проверить инвертированное положение"
        in normalized_skill
    )
    assert "остаточный список" in normalized_skill
    assert "`missing_matrix_items`" in normalized_skill
    assert "исключает `extra_in_contract`" in normalized_skill
    assert '"differences"' in skill
    assert "deviation_matrix_ids" not in skill
    assert "relation_type" not in skill
    assert "contract_locator" not in skill
    assert '"contract_evidence"' in skill
    assert '"matrix_evidence"' in skill
    assert '"shared_scope"' in skill
    assert all(
        status in skill for status in ("aligned", "deviation", "extra_in_contract")
    )
    assert (
        "/skills/integrated-contract-analysis/references/mapping-calibration.md"
        in skill
    )
    assert (
        "/skills/integrated-contract-analysis/references/status-calibration.md"
        in skill
    )
    mapping_calibration = (
        main.ANALYSIS_SKILL_SOURCE / "references" / "mapping-calibration.md"
    )
    status_calibration = (
        main.ANALYSIS_SKILL_SOURCE / "references" / "status-calibration.md"
    )
    assert mapping_calibration.is_file()
    assert status_calibration.is_file()
    for forbidden in ("gold", "KAVKAZ", "Кавказ", ".xlsx"):
        assert forbidden.casefold() not in skill.casefold()


def test_run_contract_uses_analysis_then_conclusion() -> None:
    prompt = main.ANALYSIS_SYSTEM_PROMPT.lower()
    normalized_prompt = " ".join(prompt.split())
    user_prompt = main.RUN_PROMPT.lower()
    assert "agents.md" not in prompt
    assert "сначала ровно один раз вызови `analyzer`" in normalized_prompt
    assert "ровно один раз вызови `final-reviewer`" in normalized_prompt
    assert "analyzer → final-reviewer" in user_prompt
    assert "/inputs/contract.txt" in user_prompt
    assert "/inputs/matrix.json" in user_prompt
    assert "/outputs/working/fragments/full-analysis.json" in user_prompt
    assert "/outputs/working/analysis.json" in user_prompt
    assert "/outputs/working/final-result.json" in user_prompt
    assert "все доказанные deviations" in user_prompt
    combined = "\n".join((prompt, user_prompt))
    for inactive in (
        "/outputs/result.json",
        "/outputs/working/mapping.json",
        "/outputs/working/status.json",
    ):
        assert inactive not in combined


def test_prepare_workspace_installs_skills_and_inputs(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    workspace, output = main.prepare_workspace(
        contract, matrix, tmp_path / "published" / "result.json"
    )
    try:
        assert output == (tmp_path / "published" / "result.json").resolve()
        assert not (workspace / "AGENTS.md").exists()
        assert (workspace / "inputs" / "contract.txt").read_bytes() == contract.read_bytes()
        assert (workspace / "inputs" / "matrix.json").read_bytes() == matrix.read_bytes()
        assert (workspace / "outputs" / "working").is_dir()
        assert (workspace / "outputs" / "working" / "fragments").is_dir()
        analysis = workspace / "skills" / "integrated-contract-analysis" / "SKILL.md"
        assert analysis.is_file()
        assert (
            analysis.parent / "references" / "mapping-calibration.md"
        ).is_file()
        assert (
            analysis.parent / "references" / "status-calibration.md"
        ).is_file()
        final_review = workspace / "skills" / "final-finding-review" / "SKILL.md"
        assert final_review.is_file()
        assert (
            final_review.parent / "references" / "selection-cookbook.md"
        ).is_file()
        assert (
            final_review.parent / "scripts" / "prepare_candidates.py"
        ).is_file()
        assert not (workspace / "skills" / "contract-mapping").exists()
        assert not (workspace / "skills" / "contract-group-status").exists()
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


def test_resolve_analysis_output_validates_path(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    conclusion = tmp_path / "result.json"
    resolved = main.resolve_analysis_output(
        tmp_path / "artifacts" / "analysis.json",
        contract=contract,
        matrix=matrix,
        conclusion_output=conclusion,
    )
    assert resolved == (tmp_path / "artifacts" / "analysis.json").resolve()
    assert resolved.parent.is_dir()

    with pytest.raises(ValueError, match="must differ"):
        main.resolve_analysis_output(
            conclusion,
            contract=contract,
            matrix=matrix,
            conclusion_output=conclusion,
        )
    with pytest.raises(ValueError, match="must be a .json"):
        main.resolve_analysis_output(
            tmp_path / "analysis.txt",
            contract=contract,
            matrix=matrix,
            conclusion_output=conclusion,
        )


def test_build_agent_registers_analyzer_and_final_reviewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class FakeModel:
        model_name = "deepseek-v4-pro"

    model = FakeModel()

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda: model)
    backend = main.build_backend(tmp_path)

    main.build_agent(backend)

    assert captured["name"] == "integrated-contract-analysis"
    assert captured["model"] is model
    assert captured["backend"] is backend
    assert "memory" not in captured
    assert captured["skills"] == [
        "/skills/integrated-contract-analysis/",
        "/skills/final-finding-review/",
    ]
    assert captured["system_prompt"] == main.ANALYSIS_SYSTEM_PROMPT
    subagents = captured["subagents"]
    assert isinstance(subagents, list) and len(subagents) == 2
    analyzer = subagents[0]
    assert analyzer["name"] == "analyzer"
    assert analyzer["system_prompt"] == main.ANALYZER_SYSTEM_PROMPT
    assert analyzer["skills"] == ["/skills/integrated-contract-analysis/"]
    assert "tools" not in analyzer
    assert "/outputs/working/analysis.json" in main.ANALYZER_SYSTEM_PROMPT
    assert "не создавай" in main.ANALYZER_SYSTEM_PROMPT.lower()
    assert "/outputs/working/fragments/full-analysis.json" in (
        main.ANALYZER_SYSTEM_PROMPT
    )
    assert "полный результат анализа" in main.ANALYZER_SYSTEM_PROMPT
    reviewer = subagents[1]
    assert reviewer["name"] == "final-reviewer"
    assert reviewer["system_prompt"] == main.FINAL_REVIEWER_SYSTEM_PROMPT
    assert reviewer["skills"] == ["/skills/final-finding-review/"]
    assert "tools" not in reviewer
    assert "/outputs/working/analysis.json" in main.FINAL_REVIEWER_SYSTEM_PROMPT
    assert "/outputs/working/final-result.json" in (
        main.FINAL_REVIEWER_SYSTEM_PROMPT
    )
    assert "/inputs/contract.txt" in main.FINAL_REVIEWER_SYSTEM_PROMPT
    assert "/inputs/matrix.json" in main.FINAL_REVIEWER_SYSTEM_PROMPT
    assert "не меняй analysis" in main.FINAL_REVIEWER_SYSTEM_PROMPT.lower()
    assert len(main.FINAL_REVIEWER_SYSTEM_PROMPT.split()) < 60


def test_prepare_candidates_script_preserves_only_reviewable_sources(
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "analysis.json"
    output = tmp_path / "review-candidates.json"
    aligned = {
        "contract_id": "1.1",
        "candidates": [],
        "status": "aligned",
    }
    deviation = {
        "contract_id": "2.1",
        "candidates": [{"matrix_id": "3.1"}],
        "status": "deviation",
        "differences": [{"matrix_id": "3.1", "reason": "отличие"}],
    }
    extra = {
        "contract_id": "4.1",
        "candidates": [],
        "status": "extra_in_contract",
        "contract_evidence": "новая обязанность",
        "reason": "аналога нет",
    }
    missing = {
        "matrix_id": "5.1",
        "matrix_evidence": "обязательное требование",
        "applicability_evidence": "применимо",
        "reason": "покрытие отсутствует",
    }
    analysis.write_text(
        json.dumps(
            {
                "schema_version": "analysis.v3",
                "completion_status": "complete",
                "groups": [aligned, deviation, extra],
                "missing_matrix_items": [missing],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    script = (
        main.FINAL_REVIEW_SKILL_SOURCE / "scripts" / "prepare_candidates.py"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--analysis",
            str(analysis),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "review-candidates.v1"
    assert payload["completion_status"] == "complete"
    assert payload["counts"] == {
        "deviation": 1,
        "extra_in_contract": 1,
        "missing_in_contract": 1,
        "total": 3,
    }
    assert [item["source"] for item in payload["candidates"]] == [
        deviation,
        extra,
        missing,
    ]
    assert len(
        {item["finding_id"] for item in payload["candidates"]}
    ) == 3


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
        "open('/outputs/working/analysis.json'); "
        "open('/skills/integrated-contract-analysis/SKILL.md')\""
    )
    assert main.WindowsPowerShellBackend._normalize_virtual_shell_paths(command) == (
        "python -c \"open('inputs/contract.txt'); "
        "open('outputs/working/analysis.json'); "
        "open('skills/integrated-contract-analysis/SKILL.md')\""
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


def test_analysis_validator_rejects_deviation_outside_candidates(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "analysis.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "analysis.v3",
                "completion_status": "complete",
                "groups": [
                    {
                        "contract_id": "4.3",
                        "candidates": [
                            {
                                "matrix_id": "2.1",
                                "shared_scope": "приём карт",
                                "contract_evidence": "цитата",
                                "matrix_evidence": "цитата",
                            }
                        ],
                        "status": "deviation",
                        "differences": [
                            {
                                "matrix_id": "3.1",
                                "matrix_quote": "цитата матрицы",
                                "contract_quote": "цитата договора",
                                "reason": "установленное отличие",
                            }
                        ],
                    }
                ],
                "missing_matrix_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="non-candidate matrix IDs"):
        main.validate_analysis_artifact(artifact)


def test_analysis_validator_requires_extra_evidence(tmp_path: Path) -> None:
    artifact = tmp_path / "analysis.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "analysis.v3",
                "completion_status": "complete",
                "groups": [
                    {
                        "contract_id": "4.5.1",
                        "candidates": [],
                        "status": "extra_in_contract",
                        "reason": "аналог не найден",
                    }
                ],
                "missing_matrix_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="invalid contract_evidence"):
        main.validate_analysis_artifact(artifact)


def test_conclusion_validator_rejects_empty_or_wrong_side_fields(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "conclusion.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "conclusion.v1",
                "completion_status": "complete",
                "findings": [
                    {
                        "status": "extra_in_contract",
                        "contract_items": [{"id": "4.5.1", "text": "Обязанность"}],
                        "matrix_items": [],
                        "comment": "Самостоятельная обязанность Банка",
                        "evidence": [
                            {
                                "contract_id": "4.5.1",
                                "contract_quote": "Обязанность",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="must omit matrix_items"):
        main.validate_conclusion_artifact(artifact)


def test_main_publishes_only_conclusion_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"

    def fake_run_agent(workspace: Path) -> None:
        (workspace / "outputs" / "working" / "analysis.json").write_text(
            json.dumps(
                {
                    "schema_version": "analysis.v3",
                    "completion_status": "complete",
                    "groups": [
                        {
                            "contract_id": "1.1",
                            "candidates": [
                                {
                                    "matrix_id": "2.1",
                                    "shared_scope": "оказание услуг",
                                    "contract_evidence": "Банк оказывает услуги",
                                    "matrix_evidence": "Банк оказывает услуги",
                                }
                            ],
                            "status": "aligned",
                        }
                    ],
                    "missing_matrix_items": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workspace / "outputs" / "working" / "final-result.json").write_text(
            json.dumps(
                {
                    "schema_version": "conclusion.v1",
                    "completion_status": "complete",
                    "findings": [
                        {
                            "status": "deviation",
                            "contract_items": [
                                {"id": "1.1", "text": "Банк оказывает услуги"}
                            ],
                            "matrix_items": [
                                {"id": "2.1", "text": "Банк оказывает услуги"}
                            ],
                            "comment": "Изменён срок оказания услуг",
                            "evidence": [
                                {
                                    "matrix_id": "2.1",
                                    "matrix_quote": "в течение 5 дней",
                                    "contract_quote": "в течение 10 дней",
                                }
                            ],
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
    assert result["schema_version"] == "conclusion.v1"
    assert result["findings"][0]["status"] == "deviation"
    assert sorted(path.name for path in output.parent.iterdir()) == ["result.json"]


def test_main_optionally_publishes_analysis_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    conclusion_output = tmp_path / "published" / "result.json"
    analysis_output = tmp_path / "published" / "analysis.json"
    analysis_payload = {
        "schema_version": "analysis.v3",
        "completion_status": "complete",
        "groups": [
            {
                "contract_id": "1.1",
                "candidates": [],
                "status": "extra_in_contract",
                "contract_evidence": "Банк оказывает услуги",
                "reason": "Аналог отсутствует",
            }
        ],
        "missing_matrix_items": [],
    }
    conclusion_payload = {
        "schema_version": "conclusion.v1",
        "completion_status": "complete",
        "findings": [
            {
                "status": "extra_in_contract",
                "contract_items": [
                    {"id": "1.1", "text": "Банк оказывает услуги"}
                ],
                "comment": "Аналог отсутствует",
                "evidence": [
                    {
                        "contract_id": "1.1",
                        "contract_quote": "Банк оказывает услуги",
                    }
                ],
            }
        ],
    }

    def fake_run_agent(workspace: Path) -> None:
        working = workspace / "outputs" / "working"
        (working / "analysis.json").write_text(
            json.dumps(analysis_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (working / "final-result.json").write_text(
            json.dumps(conclusion_payload, ensure_ascii=False),
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
            str(conclusion_output),
            "--analysis-output",
            str(analysis_output),
        ]
    )

    assert exit_code == 0
    assert json.loads(analysis_output.read_text(encoding="utf-8")) == analysis_payload
    assert (
        json.loads(conclusion_output.read_text(encoding="utf-8"))
        == conclusion_payload
    )
