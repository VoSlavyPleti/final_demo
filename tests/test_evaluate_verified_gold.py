from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.evaluate_verified_gold import evaluate


BENCHMARKS = Path(__file__).parents[1] / "benchmarks"
CURRENT_GOLD_FILES = (
    "kaluga_adjudicated_gold.v1.json",
    "kuzbas_adjudicated_gold.v3.json",
    "irkutsk_adjudicated_gold.v7.json",
    "altai_adjudicated_gold.v3.json",
    "kavkaz_adjudicated_gold.v3.json",
)


def test_evaluator_preserves_repeated_contract_numbers_by_occurrence() -> None:
    gold = {
        "schema_version": "gold",
        "contract_groups": [
            {
                "contract_id": "9.1",
                "source_locator": "first",
                "matrix_ids": ["9.1"],
                "status": "deviation",
            },
            {
                "contract_id": "9.1",
                "source_locator": "second",
                "matrix_ids": ["9.3"],
                "status": "aligned",
            },
        ],
        "missing_matrix_ids": [],
    }
    prediction = {
        "schema_version": "contract-matrix-map.v6",
        "contract_items": [
            {
                "contract_id": "9.1",
                "source_locator": "претензия",
                "matrix_ids": ["9.1"],
                "status": "deviation",
            },
            {
                "contract_id": "9.1",
                "source_locator": "суд",
                "matrix_ids": ["9.3"],
                "status": "aligned",
            },
        ],
        "matrix_items": [],
    }

    metrics = evaluate(gold, prediction)

    assert metrics["soft_mapping"] == {"hits": 2, "total": 2, "accuracy": 1.0}
    assert metrics["status_accuracy"] == {"hits": 2, "total": 2, "accuracy": 1.0}


def test_evaluator_matches_unnumbered_appendix_provisions_by_occurrence() -> None:
    gold = {
        "contract_groups": [
            {
                "contract_id": "Приложение №1",
                "source_locator": "плата",
                "matrix_ids": ["6.1"],
                "status": "deviation",
            },
            {
                "contract_id": "Приложение №1",
                "source_locator": "оборудование",
                "matrix_ids": [],
                "status": "extra_in_contract",
            },
        ],
        "missing_matrix_ids": [],
    }
    prediction = {
        "contract_items": [
            {
                "contract_id": "Приложение №1: плата",
                "matrix_ids": ["6.1"],
                "status": "deviation",
            },
            {
                "contract_id": "Приложение №1: оборудование",
                "matrix_ids": [],
                "status": "extra_in_contract",
            },
        ],
        "matrix_items": [],
    }

    metrics = evaluate(gold, prediction)

    assert metrics["soft_mapping"]["accuracy"] == 1.0
    assert metrics["status_accuracy"]["accuracy"] == 1.0


def test_evaluator_uses_appendix_context_for_reused_numeric_ids() -> None:
    gold = {
        "contract_groups": [
            {
                "contract_id": "1.1",
                "source_locator": "Приложение №3, спецификация",
                "matrix_ids": ["6.1"],
                "status": "deviation",
            },
            {
                "contract_id": "1.1",
                "source_locator": "Приложение №4, заверение",
                "matrix_ids": ["4.2.15"],
                "status": "deviation",
            },
        ],
        "missing_matrix_ids": [],
    }
    prediction = {
        "contract_items": [
            {
                "contract_id": "1.1",
                "matrix_ids": [],
                "status": "not_applicable",
            },
            {
                "contract_id": "1.1",
                "source_locator": "Приложение № 3, таблица",
                "matrix_ids": ["6.1"],
                "status": "deviation",
            },
            {
                "contract_id": "1.1",
                "source_locator": "Приложение № 4, раздел 2",
                "matrix_ids": ["4.2.15"],
                "status": "deviation",
            },
        ],
        "matrix_items": [],
    }

    metrics = evaluate(gold, prediction)

    assert metrics["soft_mapping"]["accuracy"] == 1.0
    assert metrics["status_accuracy"]["accuracy"] == 1.0


@pytest.mark.parametrize("filename", CURRENT_GOLD_FILES)
def test_current_gold_has_one_status_per_unique_source_occurrence(
    filename: str,
) -> None:
    payload = json.loads((BENCHMARKS / filename).read_text(encoding="utf-8"))
    occurrences: set[tuple[str, str | None]] = set()
    mapped_matrix_ids: set[str] = set()

    for row in payload["contract_groups"]:
        occurrence = (row["contract_id"], row.get("source_locator"))
        assert occurrence not in occurrences
        occurrences.add(occurrence)
        assert isinstance(row["status"], str)
        assert row["status"] in {"aligned", "deviation", "extra_in_contract"}
        assert len(row.get("matrix_ids", [])) == len(set(row.get("matrix_ids", [])))
        mapped_matrix_ids.update(row.get("matrix_ids", []))

    assert not mapped_matrix_ids.intersection(payload["missing_matrix_ids"])


def test_kavkaz_v3_contains_adjudicated_status_boundary_corrections() -> None:
    payload = json.loads(
        (BENCHMARKS / "kavkaz_adjudicated_gold.v3.json").read_text(
            encoding="utf-8"
        )
    )
    rows = {row["contract_id"]: row for row in payload["contract_groups"]}

    assert rows["5.2.10.4"]["status"] == "deviation"
    assert rows["5.2.10"]["matrix_ids"] == ["5.1.1", "5.1.1.6", "5.1.3"]
    assert rows["5.2.10"]["status"] == "deviation"
    assert rows["5.3.4"]["matrix_ids"] == ["10.2"]
    assert rows["5.3.4"]["status"] == "deviation"
    assert rows["5.3.15"]["matrix_ids"] == ["4.2.16.2", "4.2.16.3"]
    assert rows["7.7.2"]["status"] == "deviation"
    assert rows["11.5"]["status"] == "deviation"
    assert "5.3.18" not in rows
    assert payload["missing_matrix_ids"] == [
        "2.6.3.2",
        "4.2.8",
        "5.1.7",
        "7.15",
    ]
