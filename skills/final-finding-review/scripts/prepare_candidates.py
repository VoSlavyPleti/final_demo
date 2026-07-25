from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "review-candidates.v1"
REVIEWABLE_GROUP_STATUSES = {"deviation", "extra_in_contract"}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Input file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Analysis root must be a JSON object")
    return payload


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Analysis field {key!r} must be an array")
    return value


def _required_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value


def prepare_candidates(analysis: dict[str, Any]) -> dict[str, Any]:
    if analysis.get("completion_status") != "complete":
        raise ValueError("Analysis completion_status must be 'complete'")

    groups = _require_list(analysis, "groups")
    missing_items = _require_list(analysis, "missing_matrix_items")
    candidates: list[dict[str, Any]] = []

    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"groups[{index}] must be an object")
        status = group.get("status")
        if status not in REVIEWABLE_GROUP_STATUSES:
            continue
        contract_id = _required_text(
            group.get("contract_id"), f"groups[{index}].contract_id"
        )
        candidates.append(
            {
                "finding_id": f"group:{index}:{status}:{contract_id}",
                "source_kind": "group",
                "source_index": index,
                "source": group,
            }
        )

    for index, item in enumerate(missing_items):
        if not isinstance(item, dict):
            raise ValueError(f"missing_matrix_items[{index}] must be an object")
        matrix_id = _required_text(
            item.get("matrix_id"), f"missing_matrix_items[{index}].matrix_id"
        )
        candidates.append(
            {
                "finding_id": f"missing:{index}:{matrix_id}",
                "source_kind": "missing_matrix_item",
                "source_index": index,
                "source": item,
            }
        )

    finding_ids = [item["finding_id"] for item in candidates]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("Generated finding_id values are not unique")

    return {
        "schema_version": SCHEMA_VERSION,
        "completion_status": "complete",
        "source_schema_version": analysis.get("schema_version"),
        "counts": {
            "deviation": sum(
                1
                for item in candidates
                if item["source_kind"] == "group"
                and item["source"].get("status") == "deviation"
            ),
            "extra_in_contract": sum(
                1
                for item in candidates
                if item["source_kind"] == "group"
                and item["source"].get("status") == "extra_in_contract"
            ),
            "missing_in_contract": sum(
                1
                for item in candidates
                if item["source_kind"] == "missing_matrix_item"
            ),
            "total": len(candidates),
        },
        "candidates": candidates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a compact review-candidates artifact from a complete "
            "analysis artifact without changing source findings."
        )
    )
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analysis_path = args.analysis.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if analysis_path == output_path:
        raise ValueError("Output path must differ from analysis path")

    result = prepare_candidates(_load_object(analysis_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
