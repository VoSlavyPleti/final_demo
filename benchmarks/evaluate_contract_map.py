from __future__ import annotations

import argparse
import json
from pathlib import Path


STATUS_CLASSES = ("deviation", "extra_in_contract")


def normalize_id(value: object) -> str:
    return str(value or "").strip().rstrip(".").strip()


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def binary_metrics(
    predicted: set[str],
    gold: set[str],
) -> dict:
    true_positive = sorted(predicted & gold)
    false_positive = sorted(predicted - gold)
    false_negative = sorted(gold - predicted)
    precision = ratio(len(true_positive), len(predicted))
    recall = ratio(len(true_positive), len(gold))
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall
        else None
    )
    return {
        "gold_count": len(gold),
        "predicted_count": len(predicted),
        "true_positive": len(true_positive),
        "false_positive": len(false_positive),
        "false_negative": len(false_negative),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_ids": false_positive,
        "false_negative_ids": false_negative,
    }


def evaluate(gold: dict, prediction: dict) -> dict:
    gold_rows = {
        normalize_id(row["contract_id"]): row
        for row in gold["contract_items"]
    }
    predicted_rows = {
        normalize_id(row["contract_id"]): row
        for row in prediction.get("contract_items", [])
    }

    mapping_rows = [
        row for row in gold_rows.values() if row.get("matrix_ids")
    ]
    mapping_errors: list[dict] = []
    mapping_hits = 0
    status_errors: list[dict] = []
    status_hits = 0

    for gold_row in gold_rows.values():
        contract_id = normalize_id(gold_row["contract_id"])
        predicted = predicted_rows.get(contract_id)
        predicted_status = (
            predicted.get("status") if predicted else "missing_prediction"
        )
        if predicted_status == gold_row["status"]:
            status_hits += 1
        else:
            status_errors.append(
                {
                    "contract_id": contract_id,
                    "gold_status": gold_row["status"],
                    "predicted_status": predicted_status,
                }
            )

        gold_ids = {
            normalize_id(value) for value in gold_row.get("matrix_ids", [])
        }
        if not gold_ids:
            continue
        predicted_ids = {
            normalize_id(value)
            for value in (predicted or {}).get("matrix_ids", [])
        }
        overlap = sorted(gold_ids & predicted_ids)
        if overlap:
            mapping_hits += 1
        else:
            mapping_errors.append(
                {
                    "contract_id": contract_id,
                    "gold_matrix_ids": sorted(gold_ids),
                    "predicted_matrix_ids": sorted(predicted_ids),
                }
            )

    per_class: dict[str, dict] = {}
    for status in STATUS_CLASSES:
        gold_ids = {
            contract_id
            for contract_id, row in gold_rows.items()
            if row["status"] == status
        }
        predicted_ids = {
            contract_id
            for contract_id in gold_rows
            if predicted_rows.get(contract_id, {}).get("status") == status
        }
        per_class[status] = binary_metrics(predicted_ids, gold_ids)

    gold_missing = {
        normalize_id(row["matrix_id"])
        for row in gold.get("matrix_missing_items", [])
    }
    predicted_missing = {
        normalize_id(row["matrix_id"])
        for row in prediction.get("matrix_items", [])
        if row.get("status") == "missing_in_contract"
    }
    per_class["missing_in_contract"] = binary_metrics(
        predicted_missing,
        gold_missing,
    )

    return {
        "gold_schema_version": gold.get("schema_version"),
        "prediction_schema_version": prediction.get("schema_version"),
        "population": {
            "operational_contract_items": len(gold_rows),
            "mapped_contract_items": len(mapping_rows),
            "gold_status_counts": {
                status: sum(
                    row["status"] == status for row in gold_rows.values()
                )
                for status in ("aligned", "deviation", "extra_in_contract")
            },
            "gold_missing_count": len(gold_missing),
        },
        "soft_mapping": {
            "hits": mapping_hits,
            "total": len(mapping_rows),
            "accuracy": ratio(mapping_hits, len(mapping_rows)),
        },
        "status_accuracy": {
            "hits": status_hits,
            "total": len(gold_rows),
            "accuracy": ratio(status_hits, len(gold_rows)),
        },
        "per_class": per_class,
        "mapping_errors": mapping_errors,
        "status_errors": status_errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    metrics = evaluate(gold, prediction)
    rendered = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
