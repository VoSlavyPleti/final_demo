from __future__ import annotations

import argparse
import json
from pathlib import Path


def _ids(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).rstrip(".") for value in values if value is not None}


def _candidate_ids(group: dict) -> set[str]:
    values = group.get("matrix_ids")
    if isinstance(values, list):
        return _ids(values)
    values = group.get("matrix_analogs")
    if not isinstance(values, list):
        values = group.get("candidates", [])
    return {
        str(candidate.get("matrix_id", "")).rstrip(".")
        for candidate in values
        if isinstance(candidate, dict) and candidate.get("matrix_id")
    }


def _finding_ids(finding: dict, side: str) -> set[str]:
    return {
        str(item.get("id", "")).rstrip(".")
        for item in finding.get(side, [])
        if isinstance(item, dict) and item.get("id")
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--conclusion", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("benchmark.v1.json"),
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    document = next(
        (
            item
            for item in manifest["documents"]
            if item["document_id"].casefold() == args.document.casefold()
        ),
        None,
    )
    if document is None:
        raise SystemExit(f"Unknown document: {args.document}")

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    group_values = analysis.get("contract_items")
    if not isinstance(group_values, list):
        group_values = analysis.get("comparison_groups")
    if not isinstance(group_values, list):
        group_values = analysis.get("groups", [])
    groups = {
        str(group.get("contract_id", "")).rstrip("."): group
        for group in group_values
        if isinstance(group, dict) and group.get("contract_id")
    }
    gold = document["priority_deviations"]
    rows = []
    for item in gold:
        contract_id = str(item["contract_id"]).rstrip(".")
        group = groups.get(contract_id)
        expected = _ids(item["matrix_ids"])
        predicted = _candidate_ids(group) if group else set()
        mapping_hit = bool(expected & predicted)
        status_hit = bool(group and group.get("status") == "deviation")
        rows.append(
            {
                "contract_id": contract_id,
                "gold_matrix_ids": sorted(expected),
                "predicted_matrix_ids": sorted(predicted),
                "mapping_hit": mapping_hit,
                "status_hit": status_hit,
                "joint_hit": mapping_hit and status_hit,
            }
        )

    predicted_deviations = [
        group for group in groups.values() if group.get("status") == "deviation"
    ]
    predicted_true_positive = sum(
        any(
            str(item["contract_id"]).rstrip(".")
            == str(group["contract_id"]).rstrip(".")
            and bool(_ids(item["matrix_ids"]) & _candidate_ids(group))
            for item in gold
        )
        for group in predicted_deviations
    )
    output = {
        "document_id": document["document_id"],
        "priority_count": len(rows),
        "mapping_recall": _rate(
            sum(row["mapping_hit"] for row in rows),
            len(rows),
        ),
        "deviation_status_recall": _rate(
            sum(row["status_hit"] for row in rows),
            len(rows),
        ),
        "joint_recall": _rate(sum(row["joint_hit"] for row in rows), len(rows)),
        "priority_precision_among_all_predicted_deviations": _rate(
            predicted_true_positive,
            len(predicted_deviations),
        ),
        "predicted_deviation_count": len(predicted_deviations),
        "missing_matrix_count": (
            sum(
                (
                    item.get("status")
                    if "status" in item
                    else item.get("resolution")
                )
                == "missing_in_contract"
                for item in analysis.get("matrix_items", [])
                if isinstance(item, dict)
            )
            if isinstance(analysis.get("matrix_items"), list)
            else len(analysis.get("missing_matrix_items", []))
        ),
        "extra_contract_count": (
            sum(
                item.get("status") == "extra_in_contract"
                for item in analysis.get("contract_items", [])
                if isinstance(item, dict)
            )
            if isinstance(analysis.get("contract_items"), list)
            else len(analysis.get("extra_contract_items", []))
        ),
        "errors": [row for row in rows if not row["joint_hit"]],
    }

    if args.conclusion:
        conclusion = json.loads(args.conclusion.read_text(encoding="utf-8"))
        findings = [
            finding
            for finding in conclusion.get("findings", [])
            if isinstance(finding, dict) and finding.get("status") == "deviation"
        ]
        conclusion_hits = []
        for item in gold:
            expected_contract = str(item["contract_id"]).rstrip(".")
            expected_matrix = _ids(item["matrix_ids"])
            conclusion_hits.append(
                any(
                    expected_contract
                    in _finding_ids(finding, "contract_items")
                    and bool(
                        expected_matrix
                        & _finding_ids(finding, "matrix_items")
                    )
                    for finding in findings
                )
            )
        output["conclusion_priority_recall"] = _rate(
            sum(conclusion_hits),
            len(conclusion_hits),
        )
        output["conclusion_deviation_count"] = len(findings)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
