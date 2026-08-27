from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


def normalize_id(value: Any) -> str:
    return str(value or "").strip().rstrip(".").strip()


def normalize_contract_id(value: Any) -> str:
    normalized = normalize_id(value)
    appendix = re.match(
        r"^Приложение\s*№\s*(\d+(?:\.\d+)?)(?=$|[:;,\s(])",
        normalized,
        flags=re.IGNORECASE,
    )
    if appendix:
        return f"Приложение №{appendix.group(1)}"
    return normalized


def source_context(row: dict[str, Any]) -> str | None:
    """Return a stable source region for numbers reused inside appendices."""

    for value in (row.get("source_locator"), row.get("contract_id")):
        match = re.search(
            r"Приложение\s*№\s*(\d+(?:\.\d+)?)",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        if match:
            return f"appendix:{match.group(1)}"
    return None


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def class_metrics(predicted: set[str], gold: set[str]) -> dict[str, Any]:
    true_positive = predicted & gold
    false_positive = predicted - gold
    false_negative = gold - predicted
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
        "false_positive_ids": sorted(false_positive),
        "false_negative_ids": sorted(false_negative),
    }


def _index_contract_rows(
    rows: list[dict[str, Any]], *, duplicate_ids: set[str] | None = None
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    normalized_rows = [
        row for row in rows if normalize_contract_id(row.get("contract_id"))
    ]
    counts = Counter(
        normalize_contract_id(row.get("contract_id")) for row in normalized_rows
    )
    duplicates = (
        {contract_id for contract_id, count in counts.items() if count > 1}
        if duplicate_ids is None
        else duplicate_ids
    )
    indexed: dict[str, dict[str, Any]] = {}
    occurrence_counts: Counter[tuple[str, str | None]] = Counter()
    for row in normalized_rows:
        contract_id = normalize_contract_id(row.get("contract_id"))
        context = source_context(row)
        occurrence_key = (contract_id, context)
        occurrence_counts[occurrence_key] += 1
        if context:
            key = f"{contract_id}@@{context}"
            if counts[contract_id] > 1 and occurrence_counts[occurrence_key] > 1:
                key += f"@@{occurrence_counts[occurrence_key]}"
        elif contract_id in duplicates:
            key = f"{contract_id}@@{occurrence_counts[occurrence_key]}"
        else:
            key = contract_id
        if key in indexed:
            raise ValueError(f"ambiguous contract row key: {key}")
        indexed[key] = row
    return indexed, duplicates


_LOCATOR_STOPWORDS = {
    "техничес",
    "задание",
    "требова",
    "прилож",
    "раздел",
    "пункт",
    "договора",
}


def _locator_tokens(row: dict[str, Any], *, include_comment: bool) -> set[str]:
    values = [str(row.get("source_locator") or "")]
    if include_comment:
        values.append(str(row.get("comment") or ""))
    tokens: set[str] = set()
    for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", " ".join(values).lower()):
        normalized = token.replace("ё", "е")
        if len(normalized) <= 2:
            continue
        stem = normalized[:8] if len(normalized) > 8 else normalized
        if stem not in _LOCATOR_STOPWORDS:
            tokens.add(stem)
    return tokens


def _locator_score(gold_row: dict[str, Any], predicted_row: dict[str, Any]) -> float:
    gold_tokens = _locator_tokens(gold_row, include_comment=False)
    predicted_tokens = _locator_tokens(predicted_row, include_comment=True)
    if not gold_tokens or not predicted_tokens:
        return 0.0
    return len(gold_tokens & predicted_tokens) / len(gold_tokens)


def _match_prediction_rows(
    gold_source_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Match repeated source numbers without letting headings shift occurrences."""

    gold_index, duplicate_ids = _index_contract_rows(gold_source_rows)
    gold_groups: dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]] = {}
    for key, row in gold_index.items():
        identity = (normalize_contract_id(row.get("contract_id")), source_context(row))
        gold_groups.setdefault(identity, []).append((key, row))

    prediction_groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    gold_contract_ids = {identity[0] for identity in gold_groups}
    for row in prediction_rows:
        contract_id = normalize_contract_id(row.get("contract_id"))
        if contract_id not in gold_contract_ids:
            continue
        identity = (contract_id, source_context(row))
        prediction_groups.setdefault(identity, []).append(row)

    matched: dict[str, dict[str, Any]] = {}
    for identity, gold_group in gold_groups.items():
        candidates = prediction_groups.get(identity, [])
        if not candidates:
            continue
        if len(gold_group) == 1 and len(candidates) == 1:
            matched[gold_group[0][0]] = candidates[0]
            continue

        scored = sorted(
            (
                _locator_score(gold_row, predicted_row),
                gold_index_in_group,
                predicted_index,
            )
            for gold_index_in_group, (_, gold_row) in enumerate(gold_group)
            for predicted_index, predicted_row in enumerate(candidates)
        )
        used_gold: set[int] = set()
        used_predictions: set[int] = set()
        positive_match_found = False
        for score, gold_index_in_group, predicted_index in reversed(scored):
            if score <= 0:
                break
            if (
                gold_index_in_group in used_gold
                or predicted_index in used_predictions
            ):
                continue
            key = gold_group[gold_index_in_group][0]
            matched[key] = candidates[predicted_index]
            used_gold.add(gold_index_in_group)
            used_predictions.add(predicted_index)
            positive_match_found = True

        # Legacy repeated rows sometimes have only ordinal locators. Preserve
        # source order when neither side supplies semantic locator overlap.
        if not positive_match_found:
            for (key, _), predicted_row in zip(gold_group, candidates):
                matched[key] = predicted_row

    return gold_index, matched


def evaluate(gold: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    gold_rows, predicted_rows = _match_prediction_rows(
        gold["contract_groups"], prediction.get("contract_items", [])
    )

    mapping_rows = [
        (key, row) for key, row in gold_rows.items() if row.get("matrix_ids")
    ]
    mapping_hits: set[str] = set()
    mapping_errors: list[dict[str, Any]] = []
    for contract_id, gold_row in mapping_rows:
        predicted = predicted_rows.get(contract_id, {})
        gold_ids = {normalize_id(value) for value in gold_row["matrix_ids"]}
        predicted_ids = {
            normalize_id(value) for value in predicted.get("matrix_ids", [])
        }
        if gold_ids & predicted_ids:
            mapping_hits.add(contract_id)
        else:
            mapping_errors.append(
                {
                    "contract_id": contract_id,
                    "gold_matrix_ids": sorted(gold_ids),
                    "predicted_matrix_ids": sorted(predicted_ids),
                }
            )

    status_hits = {
        contract_id
        for contract_id, gold_row in gold_rows.items()
        if predicted_rows.get(contract_id, {}).get("status")
        == gold_row.get("status")
    }
    mapped_status_hits = status_hits & mapping_hits

    per_class: dict[str, Any] = {}
    for status in ("deviation", "extra_in_contract"):
        gold_ids = {
            contract_id
            for contract_id, row in gold_rows.items()
            if row.get("status") == status
        }
        predicted_ids = {
            contract_id
            for contract_id, row in predicted_rows.items()
            if row.get("status") == status
        }
        per_class[status] = class_metrics(predicted_ids, gold_ids)

    gold_missing = {
        normalize_id(value) for value in gold.get("missing_matrix_ids", [])
    }
    predicted_missing = {
        normalize_id(row["matrix_id"])
        for row in prediction.get("matrix_items", [])
        if row.get("status") == "missing_in_contract"
    }
    per_class["missing_in_contract"] = class_metrics(
        predicted_missing, gold_missing
    )

    status_errors = [
        {
            "contract_id": contract_id,
            "gold_status": gold_row.get("status"),
            "predicted_status": predicted_rows.get(contract_id, {}).get("status"),
        }
        for contract_id, gold_row in gold_rows.items()
        if contract_id not in status_hits
    ]

    return {
            "gold_schema_version": gold.get("schema_version"),
        "prediction_schema_version": prediction.get("schema_version"),
        "population": {
            "operational_contract_items": len(gold_rows),
            "mapped_contract_items": len(mapping_rows),
            "gold_status_counts": {
                status: sum(
                    row.get("status") == status for row in gold_rows.values()
                )
                for status in ("aligned", "deviation", "extra_in_contract")
            },
            "gold_missing_count": len(gold_missing),
        },
        "soft_mapping": {
            "hits": len(mapping_hits),
            "total": len(mapping_rows),
            "accuracy": ratio(len(mapping_hits), len(mapping_rows)),
        },
        "status_accuracy": {
            "hits": len(status_hits),
            "total": len(gold_rows),
            "accuracy": ratio(len(status_hits), len(gold_rows)),
        },
        "status_accuracy_on_soft_mapped": {
            "hits": len(mapped_status_hits),
            "total": len(mapping_hits),
            "accuracy": ratio(len(mapped_status_hits), len(mapping_hits)),
        },
        "per_class": per_class,
        "mapping_errors": mapping_errors,
        "status_errors": status_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    rendered = json.dumps(evaluate(gold, prediction), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
