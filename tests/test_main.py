from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

import analyze_only
import main
import review_only


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    contract = tmp_path / "contract.txt"
    matrix = tmp_path / "matrix.json"
    contract.write_text("1.1 Банк оказывает услуги.", encoding="utf-8")
    matrix.write_text(
        json.dumps(
            [
                {
                    "number": "2.1",
                    "text": "Банк оказывает услуги.",
                    "required_type": "mandatory",
                    "main_idea": "",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return contract, matrix


def _primary_payload() -> dict:
    return {
        "schema_version": "primary-analysis.v1",
        "completion_status": "complete",
        "contract_profile": {
            "bank_aliases": ["Банк"],
            "counterparty_aliases": ["Заказчик"],
        },
        "groups": [
            {
                "contract_id": "1.1",
                "locator": "п. 1.1",
                "contract_text": "Банк оказывает услуги.",
                "candidates": [
                    {
                        "matrix_id": "2.1",
                        "matrix_text": "Банк оказывает услуги.",
                        "matched_aspects": [
                            {
                                "matrix_quote": "Банк оказывает услуги",
                                "contract_quote": "Банк оказывает услуги",
                            }
                        ],
                    }
                ],
                "status": "aligned",
            }
        ],
    }


def _complete_payload() -> dict:
    return {
        "schema_version": "complete-analysis.v1",
        "completion_status": "complete",
        "contract_profile": {
            "bank_aliases": ["Банк"],
            "counterparty_aliases": ["Заказчик"],
        },
        "groups": _primary_payload()["groups"],
        "matrix_audit": [
            {
                "matrix_id": "2.1",
                "matrix_text": "Банк оказывает услуги.",
                "required_type": "mandatory",
                "applicability": "applicable",
                "resolution": "mapped",
                "contract_ids": ["1.1"],
            }
        ],
        "coverage_audit": {
            "working_matrix_count": 1,
            "mapped_count": 1,
            "recovered_count": 0,
            "missing_count": 0,
            "optional_absent_count": 0,
            "not_applicable_count": 0,
            "unresolved_matrix_ids": [],
        },
        "recovery_notes": [],
    }


def _conclusion_payload() -> dict:
    return {
        "schema_version": "conclusion.v2",
        "completion_status": "complete",
        "findings": [
            {
                "status": "deviation",
                "issue_class": "deadline_or_date",
                "contract_items": [
                    {
                        "id": "1.1",
                        "locator": "п. 1.1",
                        "text": "Исполнить за 10 дней.",
                    }
                ],
                "matrix_items": [
                    {"id": "2.1", "text": "Исполнить за 5 дней."}
                ],
                "comment": "Срок 10 дней вместо 5 дней.",
            }
        ],
    }


def test_prepare_workspace_mounts_inputs_and_three_role_skills(
    tmp_path: Path,
) -> None:
    contract, matrix = _inputs(tmp_path)
    workspace, output = main.prepare_workspace(
        contract,
        matrix,
        tmp_path / "published" / "result.json",
    )
    try:
        assert output == (tmp_path / "published" / "result.json").resolve()
        assert (workspace / "inputs" / "contract.txt").read_text(
            encoding="utf-8"
        ) == contract.read_text(encoding="utf-8")
        assert json.loads(
            (workspace / "inputs" / "matrix.json").read_text(encoding="utf-8")
        )[0]["number"] == "2.1"
        for source in main.SKILL_SOURCES:
            mounted = workspace / "skills" / source.name
            assert (mounted / "SKILL.md").is_file()
            assert list((mounted / "references").glob("*.md"))
        assert not (workspace / "AGENTS.md").exists()
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
def test_prepare_workspace_rejects_invalid_extensions(
    tmp_path: Path,
    contract_name: str,
    matrix_name: str,
    output_name: str,
    message: str,
) -> None:
    contract = tmp_path / contract_name
    matrix = tmp_path / matrix_name
    contract.write_text("text", encoding="utf-8")
    matrix.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        main.prepare_workspace(contract, matrix, tmp_path / output_name)


def test_publish_outputs_accept_relative_and_absolute_paths(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    conclusion = (tmp_path / "result.json").resolve()
    primary, analysis = main.resolve_publish_outputs(
        primary_path=tmp_path / "primary.json",
        analysis_path=tmp_path / "analysis.json",
        contract=contract,
        matrix=matrix,
        conclusion_output=conclusion,
    )
    assert primary == (tmp_path / "primary.json").resolve()
    assert analysis == (tmp_path / "analysis.json").resolve()


def test_publish_outputs_reject_path_collision(tmp_path: Path) -> None:
    contract, matrix = _inputs(tmp_path)
    with pytest.raises(ValueError, match="must differ"):
        main.resolve_publish_outputs(
            primary_path=tmp_path / "same.json",
            analysis_path=tmp_path / "same.json",
            contract=contract,
            matrix=matrix,
            conclusion_output=(tmp_path / "result.json").resolve(),
        )


def test_structural_artifact_validators_accept_agent_semantics(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary.json"
    complete = tmp_path / "complete.json"
    conclusion = tmp_path / "conclusion.json"
    primary.write_text(
        json.dumps(_primary_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    complete.write_text(
        json.dumps(_complete_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    conclusion.write_text(
        json.dumps(_conclusion_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    assert main.validate_primary_artifact(primary)["groups"][0]["status"] == "aligned"
    assert (
        main.validate_complete_artifact(complete)["matrix_audit"][0]["resolution"]
        == "mapped"
    )
    assert (
        main.validate_conclusion_artifact(conclusion)["findings"][0]["issue_class"]
        == "deadline_or_date"
    )


@pytest.mark.parametrize(
    ("validator", "payload"),
    [
        (
            main.validate_primary_artifact,
            {
                "schema_version": "analysis.v4",
                "completion_status": "complete",
                "groups": [],
            },
        ),
        (
            main.validate_complete_artifact,
            {
                "schema_version": "complete-analysis.v1",
                "completion_status": "in_progress",
                "groups": [],
                "matrix_audit": [],
            },
        ),
        (
            main.validate_conclusion_artifact,
            {
                "schema_version": "conclusion.v2",
                "completion_status": "complete",
            },
        ),
    ],
)
def test_structural_artifact_validators_reject_invalid_contracts(
    tmp_path: Path,
    validator,
    payload: dict,
) -> None:
    artifact = tmp_path / f"{uuid.uuid4().hex}.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        validator(artifact)


def test_build_agent_registers_three_explicit_role_skills(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda: object())
    main.build_agent(main.build_backend(tmp_path))

    assert captured["name"] == "contract-review-orchestrator"
    assert captured["skills"] == []
    assert captured["system_prompt"] == main.ORCHESTRATOR_SYSTEM_PROMPT
    roles = captured["subagents"]
    assert [role["name"] for role in roles] == [
        "primary-analyzer",
        "matrix-gap-recovery",
        "final-selector",
    ]
    assert [role["skills"] for role in roles] == [
        ["/skills/primary-contract-analysis/"],
        ["/skills/matrix-gap-recovery/"],
        ["/skills/final-finding-selection/"],
    ]
    assert all("tools" not in role for role in roles)


def test_prompts_keep_routing_separate_from_domain_methodology() -> None:
    prompt = main.ORCHESTRATOR_SYSTEM_PROMPT.lower()
    assert all(
        path in main.ORCHESTRATOR_SYSTEM_PROMPT
        for path in (
            "/inputs/contract.txt",
            "/inputs/matrix.json",
            "/outputs/working/primary-analysis.json",
            "/outputs/working/complete-analysis.json",
            "/outputs/working/final-result.json",
        )
    )
    for domain_rule in (
        "main_idea",
        "aligned",
        "deviation",
        "срок",
        "сумм",
        "инвер",
        "required_type",
    ):
        assert domain_rule not in prompt
    assert "не выполняй юридический анализ" in " ".join(prompt.split())
    assert len(main.PRIMARY_SYSTEM_PROMPT.split()) < 75
    assert len(main.GAP_SYSTEM_PROMPT.split()) < 60
    assert len(main.SELECTION_SYSTEM_PROMPT.split()) < 55
    assert "/outputs/working/primary-analysis.json" in main.PRIMARY_SYSTEM_PROMPT
    assert "/outputs/working/complete-analysis.json" in main.GAP_SYSTEM_PROMPT
    assert "/outputs/working/final-result.json" in main.SELECTION_SYSTEM_PROMPT


def test_skill_layout_has_no_legacy_or_gold_references() -> None:
    assert not (main.PROJECT_ROOT / "AGENTS.md").exists()
    assert not (
        main.PROJECT_ROOT / "skills" / "integrated-contract-analysis" / "SKILL.md"
    ).exists()
    assert not (
        main.PROJECT_ROOT / "skills" / "final-finding-review" / "SKILL.md"
    ).exists()
    for skill in main.SKILL_SOURCES:
        skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert skill_text.startswith("---\n")
        assert "description:" in skill_text
        reference_files = list((skill / "references").glob("*.md"))
        assert reference_files
        combined = skill_text + "\n".join(
            path.read_text(encoding="utf-8") for path in reference_files
        )
        assert "gold" not in combined.lower()
        assert "kavkaz" not in combined.lower()
    primary_skill = (
        main.PRIMARY_SKILL_SOURCE / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "/outputs/working/primary-parts/" in primary_skill
    assert "не только начальный" in primary_skill


def test_versioned_priority_benchmark_has_five_documents_and_31_cases() -> None:
    manifest = json.loads(
        (
            main.PROJECT_ROOT / "benchmarks" / "benchmark.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "contract-review-benchmark.v1"
    assert len(manifest["documents"]) == 5
    assert (
        sum(
            len(document["priority_deviations"])
            for document in manifest["documents"]
        )
        == 31
    )
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
    assert all(
        event in trace
        for event in ("model_start", "model_end", "tool_start", "tool_end")
    )
    assert "read_file" in trace
    assert "secret" not in trace


def test_windows_virtual_path_normalization() -> None:
    command = (
        "python -c \"open('/inputs/contract.txt'); "
        "open('/outputs/working/primary-analysis.json'); "
        "open('/skills/primary-contract-analysis/SKILL.md')\""
    )
    assert main.WindowsPowerShellBackend._normalize_virtual_shell_paths(command) == (
        "python -c \"open('inputs/contract.txt'); "
        "open('outputs/working/primary-analysis.json'); "
        "open('skills/primary-contract-analysis/SKILL.md')\""
    )


@pytest.mark.skipif(main.os.name != "nt", reason="Windows backend test")
def test_windows_backend_execute_preserves_utf8_and_cwd(tmp_path: Path) -> None:
    response = main.build_backend(tmp_path).execute(
        "Write-Output 'Привет'; (Get-Location).Path"
    )
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


def test_main_publishes_three_artifacts_without_semantic_postprocessing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract, matrix = _inputs(tmp_path)
    conclusion_output = tmp_path / "published" / "result.json"
    primary_output = tmp_path / "published" / "primary.json"
    analysis_output = tmp_path / "published" / "analysis.json"

    def fake_run_agent(workspace: Path, **kwargs) -> None:
        working = workspace / "outputs" / "working"
        (working / "primary-analysis.json").write_text(
            json.dumps(_primary_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        (working / "complete-analysis.json").write_text(
            json.dumps(_complete_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        (working / "final-result.json").write_text(
            json.dumps(_conclusion_payload(), ensure_ascii=False),
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
            "--primary-output",
            str(primary_output),
            "--analysis-output",
            str(analysis_output),
        ]
    )

    assert exit_code == 0
    assert json.loads(primary_output.read_text(encoding="utf-8")) == _primary_payload()
    assert json.loads(analysis_output.read_text(encoding="utf-8")) == _complete_payload()
    assert json.loads(
        conclusion_output.read_text(encoding="utf-8")
    ) == _conclusion_payload()


def test_analyze_only_uses_production_custom_role_topology(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "primary.json"
    observed: dict = {}

    def fake_run_agent(workspace: Path, **kwargs) -> None:
        observed.update(kwargs)
        (workspace / main.PRIMARY_ARTIFACT).write_text(
            json.dumps(_primary_payload(), ensure_ascii=False),
            encoding="utf-8",
        )

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    assert (
        analyze_only.run(
            [
                "--contract",
                str(contract),
                "--matrix",
                str(matrix),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert observed == {
        "run_prompt": main.PRIMARY_ONLY_RUN_PROMPT,
        "system_prompt": main.PRIMARY_ONLY_ORCHESTRATOR_SYSTEM_PROMPT,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == _primary_payload()


def test_review_only_accepts_complete_analysis_without_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "complete.json"
    output = tmp_path / "result.json"
    analysis.write_text(
        json.dumps(_complete_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run_selector(workspace: Path) -> None:
        assert (workspace / main.COMPLETE_ARTIFACT).is_file()
        (workspace / main.FINAL_ARTIFACT).write_text(
            json.dumps(_conclusion_payload(), ensure_ascii=False),
            encoding="utf-8",
        )

    monkeypatch.setattr(review_only, "run_selector", fake_run_selector)
    assert (
        review_only.run(
            [
                "--analysis",
                str(analysis),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8")) == _conclusion_payload()
