from __future__ import annotations

import json
from pathlib import Path
import shutil

import httpx
import pytest

import main


def _result_payload() -> dict:
    return {
        "schema_version": "contract-matrix-map.v3",
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
    }


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
        assert (mounted / "references" / "calibration.md").is_file()
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


def test_result_validator_accepts_full_map(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(_result_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    payload = main.validate_result_artifact(path)
    assert payload["contract_items"][0]["status"] == "aligned"
    assert payload["contract_items"][1]["status"] == "extra_in_contract"
    assert payload["matrix_items"][0]["status"] == "missing_in_contract"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(schema_version="conclusion.v2"),
        lambda p: p.update(completion_status="failed"),
        lambda p: p.pop("contract_items"),
        lambda p: p["contract_items"][0].update(status="unknown"),
        lambda p: p["contract_items"][0].update(matrix_ids="invalid"),
        lambda p: p["matrix_items"][0].update(matrix_id="2.1"),
        lambda p: p["contract_items"].append(p["contract_items"][0].copy()),
        lambda p: p.pop("matrix_items"),
        lambda p: p["matrix_items"][0].update(status="unknown"),
        lambda p: p["matrix_items"].append(p["matrix_items"][0].copy()),
    ],
)
def test_result_validator_rejects_invalid_structure(
    tmp_path: Path, mutation
) -> None:
    payload = _result_payload()
    mutation(payload)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError):
        main.validate_result_artifact(path)


def test_build_agent_registers_only_root_domain_skill(
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


def test_prompts_are_short_and_do_not_prescribe_pipeline() -> None:
    assert main.RUN_PROMPT == (
        "Сравни проект договора с банковской матрицей.\n"
        "Сохрани результат в `/outputs/result.json`."
    )
    system = main.AGENT_SYSTEM_PROMPT.lower()
    assert "/outputs/result.json" in system
    assert "analysis.json" not in system
    assert "skill.md" not in system
    assert "subagent использовать этот же skill" in system
    assert "валид" not in system
    assert "повторн" not in system
    assert "проверь" not in system
    assert "самостоятель" not in system
    for legacy_stage in ("primary", "gap", "selector", "pipeline"):
        assert legacy_stage not in main.RUN_PROMPT.lower()
    assert len(main.AGENT_SYSTEM_PROMPT.split()) < 90


def test_skill_defines_one_artifact_and_requested_columns() -> None:
    skill = main.DOMAIN_SKILL_SOURCE / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    calibration = (
        skill.parent / "references" / "calibration.md"
    ).read_text(encoding="utf-8")
    combined = text + calibration

    assert text.startswith("---\n")
    assert "/outputs/result.json" in text
    assert "analysis.json" not in text
    assert '"contract_items"' in text
    assert '"matrix_items"' in text
    assert '"contract_text"' in text
    assert '"matrix_text"' in text
    assert '"matrix_ids"' in text
    assert '"applicability"' not in text
    assert '"resolution"' not in text
    assert '"coverage_audit"' not in text
    assert '"contract_profile"' not in text
    assert "каждый нумерованный пункт основного текста договора" in text
    assert "все рабочие строки матрицы" in text
    assert "все и только применимые mandatory-строки" in text
    assert "объединять несколько нумерованных пунктов" in text.lower()
    assert "нумерация разделов, полей и таблиц внутри приложений" in text.lower()
    assert "not_applicable" in text
    assert "неактивированной продуктовой ветки" in text.lower()
    assert "любое отличие конкретного срока" in text.lower()
    assert "составить множество использованных" in text.lower()
    assert "функционально изменённый" in text.lower()
    assert "ненумерованный текст после пункта" in text.lower()
    assert "меняет юридический эффект" in text.lower()
    assert "общая применимость" in text.lower()
    assert "незаполненное наименование продукта" in text.lower()
    assert "worked comparisons" in combined.lower()
    assert "изменённая сумма" in calibration.lower()
    assert "совпадающий срок" in calibration.lower()
    assert "изменённый триггер" in calibration.lower()
    assert "перенесённая обязанность" in calibration.lower()
    assert len(calibration.split()) <= 250
    assert "не выводится" not in combined.lower()
    assert "игнорировать" not in combined.lower()
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


def test_run_agent_retries_same_thread(monkeypatch: pytest.MonkeyPatch) -> None:
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

    fake = FakeAgent()
    monkeypatch.setattr(main, "build_backend", lambda workspace: object())
    monkeypatch.setattr(main, "build_agent", lambda backend, checkpointer: fake)
    main.run_agent(
        Path("."),
        max_retries=1,
        thread_id="stable",
        sleep=lambda _: None,
    )
    assert len(fake.calls) == 2
    assert all(
        call[1]["configurable"]["thread_id"] == "stable" for call in fake.calls
    )
    assert main.RUN_PROMPT in fake.calls[0][0]["messages"][0]["content"]
    assert "/outputs/result.json" in fake.calls[1][0]["messages"][0]["content"]


def test_main_publishes_single_agent_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract, matrix = _inputs(tmp_path)
    output = tmp_path / "published" / "result.json"
    seen: dict[str, Path] = {}

    def fake_run(workspace: Path, **kwargs) -> None:
        seen["workspace"] = workspace
        result = workspace / main.RESULT_ARTIFACT
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(
            json.dumps(_result_payload(), ensure_ascii=False),
            encoding="utf-8",
        )

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
