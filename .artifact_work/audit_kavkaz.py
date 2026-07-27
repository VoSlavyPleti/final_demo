import json
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "inputs" / "kavkaz.txt"
MATRIX_PATH = ROOT / "inputs" / "matrix.json"
GOLD_PATH = ROOT / "gold_results" / "KAVKAZ.xlsx"


def norm_id(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().rstrip(".")


def split_matrix_ids(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value)
    if text.strip() in {"", "—", "-"}:
        return []
    return [
        norm_id(token)
        for token in re.findall(r"\d+(?:\.\d+)+\.?", text)
        if norm_id(token)
    ]


contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
line_re = re.compile(r"(?m)^\s*(\d+(?:\.\d+)+)\.\s+")
matches = list(line_re.finditer(contract_text))
contract_points: dict[str, str] = {}
for index, match in enumerate(matches):
    point_id = norm_id(match.group(1))
    end = matches[index + 1].start() if index + 1 < len(matches) else len(contract_text)
    contract_points[point_id] = contract_text[match.start():end].strip()

matrix_rows = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
matrix_by_id = {norm_id(row["number"]): row for row in matrix_rows}

contract_gold = pd.read_excel(GOLD_PATH, sheet_name=0, header=1)
matrix_only_gold = pd.read_excel(GOLD_PATH, sheet_name=1, header=1)

gold_contract_ids = {
    norm_id(value)
    for value in contract_gold.iloc[:, 0].tolist()
    if norm_id(value)
}
mapped_matrix_ids: set[str] = set()
for value in contract_gold.iloc[:, 3].tolist():
    mapped_matrix_ids.update(split_matrix_ids(value))
matrix_only_ids = {
    norm_id(value)
    for value in matrix_only_gold.iloc[:, 0].tolist()
    if norm_id(value)
}

print("CONTRACT parsed:", len(contract_points))
print("GOLD contract rows:", len(gold_contract_ids))
print("Contract points absent from gold:", sorted(set(contract_points) - gold_contract_ids))
print("Gold IDs absent from parsed contract:", sorted(gold_contract_ids - set(contract_points)))
print()
print("MATRIX source rows:", len(matrix_by_id))
print("Mapped matrix IDs:", len(mapped_matrix_ids))
print("Matrix-only IDs:", len(matrix_only_ids))
print("Matrix IDs absent from both:", sorted(set(matrix_by_id) - mapped_matrix_ids - matrix_only_ids))
print("Unknown mapped matrix IDs:", sorted(mapped_matrix_ids - set(matrix_by_id)))
print("Unknown matrix-only IDs:", sorted(matrix_only_ids - set(matrix_by_id)))
print("Mapped and matrix-only overlap:", sorted(mapped_matrix_ids & matrix_only_ids))

status_map = {
    "✅ Соответствует": "aligned",
    "⚠️ Расхождение": "deviation",
    "❌ Нет аналога": "extra_in_contract",
}

contract_audit_lines: list[str] = []
for _, gold_row in contract_gold.iterrows():
    contract_id = norm_id(gold_row.iloc[0])
    if not contract_id:
        continue
    contract_audit_lines.append(f"\n## C {contract_id}")
    contract_audit_lines.append(
        f"STATUS: {status_map.get(str(gold_row.iloc[2]).strip(), gold_row.iloc[2])}"
    )
    contract_audit_lines.append(f"CONTRACT: {contract_points.get(contract_id, '[parse miss]')}")
    candidate_ids = split_matrix_ids(gold_row.iloc[3])
    contract_audit_lines.append(f"CANDIDATES: {', '.join(candidate_ids) or 'none'}")
    for matrix_id in candidate_ids:
        matrix_row = matrix_by_id.get(matrix_id)
        contract_audit_lines.append(
            f"M {matrix_id}: {matrix_row['text'] if matrix_row else '[unknown]'}"
        )
    comment = "" if pd.isna(gold_row.iloc[4]) else str(gold_row.iloc[4])
    contract_audit_lines.append(f"COMMENT: {comment}")

(ROOT / ".artifact_work" / "contract_audit.md").write_text(
    "\n".join(contract_audit_lines),
    encoding="utf-8",
)

matrix_only_lines: list[str] = []
for _, gold_row in matrix_only_gold.iterrows():
    matrix_id = norm_id(gold_row.iloc[0])
    if not matrix_id:
        continue
    matrix_row = matrix_by_id[matrix_id]
    matrix_only_lines.extend(
        [
            f"\n## M {matrix_id}",
            f"CATEGORY: {gold_row.iloc[2]}",
            f"REQUIRED_TYPE: {matrix_row.get('required_type')}",
            f"SELECTORS: product={matrix_row.get('only_for_product')}; lot={matrix_row.get('only_for_lot')}; payment={matrix_row.get('payment_method')}; terminal={matrix_row.get('only_for_terminal')}",
            f"MAIN_IDEA: {matrix_row.get('main_idea')}",
            f"TEXT: {matrix_row.get('text')}",
            f"COMMENT: {gold_row.iloc[3]}",
        ]
    )

(ROOT / ".artifact_work" / "matrix_only_audit.md").write_text(
    "\n".join(matrix_only_lines),
    encoding="utf-8",
)
