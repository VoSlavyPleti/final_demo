from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "irkutsk_verified_gold.v1.json"
TARGET = ROOT / "benchmarks" / "irkutsk_verified_gold.v2.json"

# These clauses either restate a general statutory rule or contain no
# independent operational duty beyond other clauses of the contract.
NON_OPERATIONAL = {
    "1.6",
    "2.2",
    "2.7",
    "4.1.7",
    "4.3.3",
    "7.8",
    "7.9",
    "8.7",
}

# Corrections supported by direct semantic comparison of the contract and
# matrix. A non-empty candidate set means the clause is not an unmapped extra.
CONTRACT_CORRECTIONS: dict[str, tuple[list[str], str]] = {
    "2.4": (["6.3"], "deviation"),
    "2.6": (["6.6"], "extra_in_contract"),
    "4.1.1": (["5.2.3", "2.1", "4.2.20.4"], "deviation"),
    "4.1.2": (["5.2.3", "4.2.20.1"], "deviation"),
    "4.1.5": (["5.2.8"], "deviation"),
    "4.1.6": (["6.3"], "extra_in_contract"),
    "4.2.1.2": (["5.1.1.1", "5.1.5"], "deviation"),
    "4.2.1.5": (["5.1.1.5", "4.2.13"], "deviation"),
    "4.2.3.1": (["5.1.8.1", "5.1.2"], "deviation"),
    "4.2.4": (["5.1.9", "5.1.10"], "deviation"),
    "4.2.5": (["5.1.11", "5.1.10"], "aligned"),
    "4.2.6": (["5.1.14", "5.1.10"], "aligned"),
    "4.3.4": (["6.3"], "aligned"),
    "4.4.9": (["4.2.10", "4.2.19"], "aligned"),
    "4.4.10": (["4.2.11", "4.2.19"], "deviation"),
    "4.4.11": (["4.2.15", "5.1.16"], "deviation"),
    "4.4.15": (["4.2.20.6", "4.2.18", "4.2.20.7"], "deviation"),
    "5.1": (["6.3", "6.4"], "aligned"),
    "5.2": (["6.3"], "deviation"),
    "5.3": (["6.4"], "deviation"),
    "5.4": (["6.4"], "deviation"),
    "6.2": (["11.3", "5.1.7", "5.2.10"], "deviation"),
    "7.1": (["7.1", "7.15"], "aligned"),
    "8.2": (["10.1", "10.3"], "deviation"),
    "8.6": (["10.2"], "deviation"),
    "12.1": (["2.3", "2.3.1", "2.3.2", "2.3.4", "2.3.5"], "deviation"),
    "12.2": (["11.10"], "deviation"),
    "12.3": (["11.9"], "aligned"),
    "12.4": (["11.1"], "deviation"),
}

# A matrix requirement is missing only when it is mandatory, applicable and
# has neither full nor partial semantic coverage anywhere in the contract.
REAL_MISSING = [
    "4.2.14",
    "4.2.16.1",
    "4.2.16.2",
    "4.2.16.3",
]


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    payload["schema_version"] = "contract-review-verified-gold.v2"
    payload["policy"] = {
        **payload["policy"],
        "audit_revision": (
            "Removed general/statutory declarations without independent "
            "operational effect; recovered semantic mappings across the whole "
            "matrix; partial coverage is deviation and is not duplicated as "
            "missing."
        ),
    }

    revised_groups: list[dict[str, object]] = []
    for row in payload["contract_groups"]:
        contract_id = row["contract_id"]
        if contract_id in NON_OPERATIONAL:
            continue
        if contract_id in CONTRACT_CORRECTIONS:
            matrix_ids, status = CONTRACT_CORRECTIONS[contract_id]
            row = {**row, "matrix_ids": matrix_ids, "status": status}
        revised_groups.append(row)

    payload["contract_groups"] = revised_groups
    payload["missing_matrix_ids"] = REAL_MISSING
    payload["excluded_contract_ids"] = sorted(
        set(payload.get("excluded_contract_ids", [])) | NON_OPERATIONAL
    )

    status_counts = Counter(row["status"] for row in revised_groups)
    payload["counts"] = {
        "operational_contract_groups": len(revised_groups),
        "mapped_contract_groups": sum(bool(row["matrix_ids"]) for row in revised_groups),
        "aligned": status_counts["aligned"],
        "deviation": status_counts["deviation"],
        "extra_in_contract": status_counts["extra_in_contract"],
        "missing_in_contract": len(REAL_MISSING),
    }

    TARGET.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(TARGET)
    print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
