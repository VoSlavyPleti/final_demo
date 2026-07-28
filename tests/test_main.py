from __future__ import annotations

import json
from pathlib import Path
import shutil

import httpx
import pytest

import main


def _result_payload() -> dict:
    return {
        "schema_version": "contract-matrix-map.v4",
        "completion_status": "complete",
        "contract_items": [
            {
                "contract_id": "1.1",
                "contract_text": "Исполнитель оказывает услуги.",
                "matrix_ids": ["2.1"],
                "status": "aligned",
                "comment": "Юридический механизм совпадает.",
            },
            {
                "contract_id": "1.2",
                "contract_text": "Новое самостоятельное обязательство.",
                "matrix_ids": [],
                "status": "extra_in_contract",
                "comment": "Юридического аналога нет.",
            }
        ],
        "matrix_items": [
            {
                "matrix_id": "3.1",
                "matrix_text": "Обязательное требование.",
                "required_type": "mandatory",
                "status": "missing_in_contract",
                "comment": "Применимо, аналог отсутствует.",
            },
        ],
        "review_items": [],
    }


def _audit_payload(**overrides) -> dict:
    payload = {
        "schema_version": "contract-review-coverage.v2",
        "completion_status": "complete",
        "source_contract_item_count": 2,
        "result_contract_item_count": 2,
        "source_contract_ids": ["1.1", "1.2"],
        "result_contract_ids": ["1.1", "1.2"],
        "contract_inventory_complete": True,
        "all_contract_items_processed": True,
        "mandatory_matrix_sweep_complete": True,
        "business_aligned_challenge_complete": True,
        "business_deviation_sweep_complete": True,
        "suppression_sweep_complete": True,
        "main_idea_evidence_check_complete": True,
        "status_audit_complete": True,
        "number_neutrality_review_complete": True,
        "mapping_cliff_review_complete": True,
        "unprocessed_contract_ids": [],
        "duplicate_contract_ids": [],
        "synthetic_contract_ids": [],
        "unresolved_sections": [],
        "blocker_count": 0,
        "blockers": [],
    }
    payload.update(overrides)
    return payload


def _status_audit_payload(**overrides) -> dict:
    payload = {
        "schema_version": "contract-review-status-audit.v1",
        "completion_status": "complete",
        "deviation_decisions": [],
        "extra_decisions": [
            {
                "contract_id": "1.2",
                "candidate_matrix_ids_checked": [],
                "operational_effect": "Самостоятельная обязанность.",
                "no_shared_business_proposition_reason": "Аналогов нет.",
                "decision": "extra_in_contract",
            }
        ],
        "missing_decisions": [
            {
                "matrix_id": "3.1",
                "semantic_candidates_checked": [],
                "same_relationship_partial_analog_found": False,
                "applicability_basis": "Применимая mandatory-строка.",
                "no_analog_reason": "Аналогов нет.",
                "decision": "missing_in_contract",
            }
        ],
        "rejected_deviation_candidates": [],
        "blocker_count": 0,
        "blockers": [],
    }
    payload.update(overrides)
    return payload


def _write_ready_artifacts(workspace: Path) -> None:
    result = workspace / main.RESULT_ARTIFACT
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(
        json.dumps(_result_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    audit = workspace / main.COVERAGE_AUDIT_ARTIFACT
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps(_audit_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    status_audit = workspace / main.STATUS_AUDIT_ARTIFACT
    status_audit.parent.mkdir(parents=True, exist_ok=True)
    status_audit.write_text(
        json.dumps(_status_audit_payload(), ensure_ascii=False),
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
        assert (mounted / "references" / "worked-examples.md").is_file()
        assert (mounted / "references" / "output-schema.md").is_file()
        assert not (mounted / "references" / "calibration.md").exists()
        assert len(list((workspace / "skills").iterdir())) == 1
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


def test_build_agent_uses_builtin_general_purpose_subagent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(main, "get_llm", lambda: object())
    backend = main.build_backend(tmp_path)
    main.build_agent(backend, checkpointer=object())

    assert captured["system_prompt"] == main.AGENT_SYSTEM_PROMPT
    assert captured["skills"] == ["/skills/contract-matrix-review/"]
    assert "subagents" not in captured
    assert "tools" not in captured


def test_prompts_define_autonomy_and_completion_contract() -> None:
    system = main.AGENT_SYSTEM_PROMPT.lower()
    user = main.RUN_PROMPT.lower()

    assert system.count("/outputs/result.json") == 1
    assert "/outputs/result.json" not in user
    assert "/outputs/working/coverage-audit.json" in system
    assert "/skills/contract-matrix-review/" in system
    assert "сам выбирай инструменты" in system
    assert "general-purpose" in system
    assert "contract-mapper" not in system
    assert "статусный артефакт" in system
    assert "blocker_count" in system
    assert "нулевой смысловой вес нумерации" in system
    assert "равны нулю" in system
    assert "сам выбирай инструменты" in system
    assert not (main.PROMPTS_ROOT / "mapping-worker-system.md").exists()

    assert "каждый собственный нумерованный" in user
    assert "не завершай" in user
    assert "missing-sweep" in user
    assert "business deviations" in user
    assert "main_idea" in system
    assert "number_neutrality_review_complete" in system


def test_skill_defines_one_artifact_and_requested_columns() -> None:
    skill = main.DOMAIN_SKILL_SOURCE / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    policy = (
        skill.parent / "references" / "business-deviation-policy.md"
    ).read_text(encoding="utf-8")
    examples = (
        skill.parent / "references" / "worked-examples.md"
    ).read_text(encoding="utf-8")
    schema = (
        skill.parent / "references" / "output-schema.md"
    ).read_text(encoding="utf-8")
    combined = text + policy + examples + schema

    assert text.startswith("---\n")
    assert "/outputs/result.json" not in combined
    assert "analysis.json" not in text
    assert '"contract_items"' in schema
    assert '"matrix_items"' in schema
    assert '"review_items"' in schema
    assert '"contract_text"' in schema
    assert '"matrix_text"' in schema
    assert '"matrix_ids"' in schema
    assert "contract-matrix-map.v4" in schema
    assert "каждый собственный номер основного текста договора" in text
    assert "не объединять разные нумерованные пункты" in text.lower()
    assert "нумерации" in text.lower()
    assert "полей и таблиц приложения" in text.lower()
    assert "not_applicable" in text
    assert "uncertain_applicability" in text
    assert "uncertain_mapping" in combined
    assert "не более трёх" in text.lower()
    assert "тому же правоотношению" in text.lower()
    assert "совпадающее положение в другом месте" in text.lower()
    assert "amount_or_rate" in policy
    assert "payment_mechanism" in policy
    assert "bank_right" in policy
    assert "required_scope" in policy
    assert "deadline_or_date" in policy
    assert "channel_or_form" in policy
    assert "data_transfer" in policy
    assert "other_material" in policy
    assert "main_idea" in text
    assert "`main_idea` не участвует в gate" in policy.lower()
    assert "ндс или налоговому режиму" in policy.lower()
    assert "не создавать `missing_in_contract`" in examples.lower()
    assert "complete_with_review" in schema
    assert "не готовить redline" not in combined.lower()
    assert "не заменять решение" not in combined.lower()
    assert "окончательным юридическим заключением" not in combined.lower()
    assert "финальная выборка" not in schema.lower()
    assert "gold" not in combined.lower()
    assert "kavkaz" not in combined.lower()


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


def test_quality_gate_requires_completed_agent_audit(tmp_path: Path) -> None:
    _write_ready_artifacts(tmp_path)
    assert main.quality_gate_failures(tmp_path) == []

    audit_path = tmp_path / main.COVERAGE_AUDIT_ARTIFACT
    audit_path.write_text(
        json.dumps(
            _audit_payload(
                blocker_count=1,
                blockers=["Раздел 6 не проверен"],
                mapping_cliff_review_complete=False,
                number_neutrality_review_complete=False,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    failures = main.quality_gate_failures(tmp_path)
    assert any("blocker_count" in failure for failure in failures)
    assert any("mapping_cliff_review_complete" in failure for failure in failures)
    assert any(
        "number_neutrality_review_complete" in failure for failure in failures
    )
    assert any("blockers is not empty" in failure for failure in failures)

    status_path = tmp_path / main.STATUS_AUDIT_ARTIFACT
    status_path.write_text(
        json.dumps(
            _status_audit_payload(
                blocker_count=1,
                blockers=["Не проверены кандидаты deviation"],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    failures = main.quality_gate_failures(tmp_path)
    assert any("status audit blocker_count" in failure for failure in failures)
    assert any("status audit blockers is not empty" in failure for failure in failures)


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
            _write_ready_artifacts(tmp_path)

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


def test_run_agent_repairs_failed_quality_gate_in_same_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, dict]] = []

        def invoke(self, payload, config):
            self.calls.append((payload, config))
            _write_ready_artifacts(tmp_path)
            if len(self.calls) == 1:
                audit = tmp_path / main.COVERAGE_AUDIT_ARTIFACT
                audit.write_text(
                    json.dumps(
                        _audit_payload(
                            blocker_count=1,
                            blockers=["Не проверен последний раздел"],
                        ),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

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
    repair_prompt = fake.calls[1][0]["messages"][0]["content"]
    assert "blocker_count is not zero" in repair_prompt
    assert "audit-файла" in repair_prompt


def test_main_publishes_single_agent_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    seen: dict[str, Path] = {}

    def fake_run(workspace: Path, **kwargs) -> None:
        seen["workspace"] = workspace
        _write_ready_artifacts(workspace)

    monkeypatch.setattr(main, "run_agent", fake_run)
    code = main.main(
        [
            "--contract",
            str(contract),
            "--matrix",
            str(matrix),
            "--output",
            str(output),
        ]
    )
    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == _result_payload()
    assert not seen["workspace"].exists()
