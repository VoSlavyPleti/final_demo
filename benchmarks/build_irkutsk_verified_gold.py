from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATED_SOURCE = ROOT / ".run" / "gold-audit-20260721" / "validated-gold.json"
OUTPUT = ROOT / "benchmarks" / "irkutsk_verified_gold.v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    validated = json.loads(VALIDATED_SOURCE.read_text(encoding="utf-8"))["irkutsk"]
    contract_groups = []
    excluded_contract_ids = []

    for row in validated["contract_rows"]:
        contract_id = row["contract_id"]
        validated_status = row["validated_status"]
        if validated_status.startswith("excluded_"):
            excluded_contract_ids.append(contract_id)
            continue

        if row["original_normalized_status"] == "deviation":
            status = "deviation"
        elif validated_status == "deviation":
            # Business benchmark keeps only deviations marked in the original
            # legal gold. Recovered semantic mappings do not create new
            # positive deviation cases.
            status = "aligned"
        else:
            status = validated_status

        contract_groups.append(
            {
                "contract_id": contract_id,
                "matrix_ids": row["validated_matrix_ids"],
                "status": status,
            }
        )

    missing_matrix_ids = [
        row["matrix_id"]
        for row in validated["matrix_rows"]
        if row["validated_status"] == "missing_in_contract"
    ]

    status_counts = {
        status: sum(row["status"] == status for row in contract_groups)
        for status in ("aligned", "deviation", "extra_in_contract")
    }
    result = {
        "schema_version": "contract-review-verified-gold.v1",
        "document_id": "IRKUTSK",
        "sources": {
            "legal_gold": {
                "path": "gold_results/irkutsk_infektsionka_analysis_v1.xlsx",
                "sha256": sha256(
                    ROOT / "gold_results" / "irkutsk_infektsionka_analysis_v1.xlsx"
                ),
            },
            "mapping_audit": {
                "path": ".run/gold-audit-20260721/validated-gold.json",
                "sha256": sha256(VALIDATED_SOURCE),
            },
            "contract": {
                "path": "irkutsk.txt",
                "sha256": sha256(ROOT / "irkutsk.txt"),
            },
            "matrix": {
                "path": "inputs/matrix.json",
                "sha256": sha256(ROOT / "inputs" / "matrix.json"),
            },
        },
        "policy": {
            "orientation": "contract",
            "soft_mapping": (
                "A mapped contract group is correct when predicted matrix_ids "
                "intersect its verified candidate pool."
            ),
            "status": (
                "Deviation labels are limited to the eleven deviations in the "
                "original legal gold; recovered semantic mappings do not add "
                "new positive deviation cases."
            ),
            "exclusions": (
                "Requisites and reference-only contact provisions are excluded."
            ),
            "missing": (
                "Only mandatory, applicable matrix requirements with no "
                "recovered semantic analog are missing."
            ),
        },
        "counts": {
            "operational_contract_groups": len(contract_groups),
            "mapped_contract_groups": sum(
                bool(row["matrix_ids"]) for row in contract_groups
            ),
            **status_counts,
            "missing_in_contract": len(missing_matrix_ids),
        },
        "contract_groups": contract_groups,
        "missing_matrix_ids": missing_matrix_ids,
        "excluded_contract_ids": excluded_contract_ids,
    }
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
