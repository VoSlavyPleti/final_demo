from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from build_adjudicated_gold import _counts, _matrix_ids, _validate_source_ids


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "inputs" / "matrix.json"


def _write(target: Path, payload: dict[str, Any]) -> Path:
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def build_kaluga() -> Path:
    source = ROOT / ".run" / "cross-eval-20260817" / "kaluga-mapped-gold.json"
    target = ROOT / "benchmarks" / "kaluga_adjudicated_gold.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))

    deviation_notes = {
        "3.1": "В активном условии договора оставлен незаполненный способ оплаты, тогда как сопоставленная строка матрицы содержит конкретные способы; C01.",
        "4.2.4": "В активном перечне материалов оставлен незаполненный способ оплаты при заполненном матричном перечне; C01.",
        "4.2.17": "Адрес направления результата PCI DSS оставлен незаполненным при конкретном матричном адресе; C01.",
        "4.2.20.1": "URL обязательного инструктажа оставлен незаполненным при конкретном матричном URL; C01.",
        "5.1.1.1": "В активном перечне недействительных операций оставлены незаполненные способы оплаты при заполненном матричном перечне; C01.",
        "5.1.1.4": "В активном перечне оспариваемых операций оставлен незаполненный способ оплаты при заполненном матричном перечне; C01.",
        "5.2.5": "URL обучающих материалов оставлен незаполненным при конкретном матричном URL; C01.",
    }

    rows: list[dict[str, Any]] = []
    for original in payload["contract_groups"]:
        row = dict(original)
        contract_id = row["contract_id"]
        if contract_id in deviation_notes:
            row.update(
                status="deviation",
                adjudication_note=deviation_notes[contract_id],
            )
        if contract_id == "11.8":
            row.update(
                matrix_ids=["11.8"],
                adjudication_note="Исправлен устаревший номер: тексту заверения соответствует действующая строка 11.8 матрицы.",
            )
        if contract_id == "11.9":
            row.update(
                matrix_ids=["11.9"],
                adjudication_note="Исправлен устаревший номер: запрету уступки соответствует действующая строка 11.9 матрицы.",
            )
        rows.append(row)

    missing = ["4.2.16.2", "2.6.3.2"]
    payload.update(
        schema_version="contract-review-adjudicated-gold.v1",
        document_id="KALUGA",
        sources={
            "contract": "kaluga.txt",
            "matrix": "inputs/matrix.json",
            "legal_workbook": "gold_results/kaluga_stomat_analysis_v1.xlsx",
        },
        policy={
            "placeholder": "A contract placeholder is deviation when the mapped matrix value is filled.",
            "mapping": "Matrix numbering has no semantic weight; current source IDs are used.",
            "missing": "Only mandatory active rows without full or partial coverage are missing.",
        },
        contract_groups=rows,
        missing_matrix_ids=missing,
        counts=_counts(rows, missing),
    )
    _validate_source_ids(rows, missing, ROOT / "kaluga.txt", MATRIX)
    return _write(target, payload)


def build_kuzbas() -> Path:
    source = ROOT / "benchmarks" / "kuzbas_adjudicated_gold.v2.json"
    target = ROOT / "benchmarks" / "kuzbas_adjudicated_gold.v3.json"
    payload = json.loads(source.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    for original in payload["contract_groups"]:
        row = dict(original)
        if row["contract_id"] == "6.2":
            row.update(
                status="deviation",
                adjudication_note=(
                    "Матрица прямо включает НДС в вознаграждение, договор прямо "
                    "устанавливает освобождение от НДС; изменён налоговый режим цены."
                ),
            )
        rows.append(row)

    # Matrix 7.3 is partially covered by contract 7.5 and its absent cap is
    # already represented as C07 deviation there; it must not also be missing.
    missing = ["2.6.3.2"]
    payload.update(
        schema_version="contract-review-adjudicated-gold.v3",
        contract_groups=rows,
        missing_matrix_ids=missing,
        counts=_counts(rows, missing),
    )
    payload["policy"]["local_antecedent"] = (
        "Global QR scope does not prove a row-local factual condition such as "
        "the absence of the enterprise's own software."
    )
    _validate_source_ids(rows, missing, ROOT / "kyzbas.txt", MATRIX)
    return _write(target, payload)


def build_kavkaz() -> Path:
    source = ROOT / ".run" / "kavkaz-current-gold.json"
    target = ROOT / "benchmarks" / "kavkaz_adjudicated_gold.v3.json"
    raw = json.loads(source.read_text(encoding="utf-8-sig"))

    excluded = {"5.3.18", "8.8"}
    corrections: dict[str, tuple[list[str], str, str]] = {
        "1.3": (
            ["3.1", "4.2.5"],
            "deviation",
            "Приём карт является частичным аналогом, но не раскрывает запрет выдачи наличных и количественное ограничение на принимаемые карты; C09.",
        ),
        "3.1": (
            ["6.21"],
            "aligned",
            "Цена заполняет открытое поле матрицы; значение НДС открыто в обоих источниках и само по себе не образует C01.",
        ),
        "4.1": (
            ["10.1"],
            "aligned",
            "Период оказания услуг и прекращение при исчерпании цены частично соответствуют сроку матрицы; заполнение открытой даты не является целевой дельтой.",
        ),
        "4.4": (
            ["3.3", "4.2.1", "4.2.12", "5.1.13", "6.5"],
            "deviation",
            "Платёжное поручение заменяет акцепт требования Банка, а письменное уведомление о реквизитах — публикацию на сайте; C03 и C05.",
        ),
        "4.5.1": (
            ["6.5"],
            "deviation",
            "Специальный ноябрьский срок относится к той же оплате и расходится с пятидневным сроком матрицы; C04.",
        ),
        "5.2.10.4": (
            ["5.1.1.4"],
            "deviation",
            "Из локального перечня оспоренных операций исключены операции SberPay/Плати QR; C09.",
        ),
        "5.2.10": (
            ["5.1.1", "5.1.1.6", "5.1.3"],
            "deviation",
            "Закрытый перечень удерживаемых сумм не содержит штраф за невозврат терминалов, предусмотренный матрицей; C09. Частичное покрытие исключает отдельный missing.",
        ),
        "5.2.9": (
            [
                "5.1.2", "5.1.8", "5.1.8.1", "5.1.8.2", "5.1.8.3",
                "5.1.8.6", "5.1.8.7", "5.1.8.8", "5.1.8.9", "5.1.8.10",
                "5.1.8.11", "5.1.8.12", "5.1.8.13",
            ],
            "deviation",
            "Общая защитная цепочка совпадает, но приостановление заменяет прекращение авторизации; C15. Отсутствие отдельного долгового триггера не создаёт missing при наличии частичного аналога.",
        ),
        "5.3.4": (
            ["10.2"],
            "deviation",
            "Право на одностороннее прекращение ограничено специальным основанием и не содержит общего права обеих сторон с уведомлением за 30 дней; C16.",
        ),
        "5.3.15": (
            ["4.2.16.2", "4.2.16.3"],
            "deviation",
            "Механизм подтверждения правомерности передачи ПДн не раскрывает требуемый срок, состав данных работников и весь предусмотренный матрицей периметр получателей; C08.",
        ),
        "7.1": (
            ["7.1", "7.3", "7.7"],
            "aligned",
            "Общая ответственность не покрывает специальную ответственность предприятия за действия персонала; ошибочная ссылка на 7.15 удалена.",
        ),
        "8.2": (
            ["7.1"],
            "aligned",
            "Ответственность исполнителя за причинённый его специалистами ущерб является частным случаем общей ответственности той же стороны.",
        ),
        "7.7.2": (
            ["7.5"],
            "deviation",
            "Для того же штрафа добавлена специальная альтернативная расчётная база и сокращена шкала при закупке за право заключения контракта; C07.",
        ),
        "8.5.7": (
            ["6.4"],
            "deviation",
            "Подписание и размещение документа приёмки относится к возврату сопоставимого приёмочного документа, но срок изменён; C14.",
        ),
        "8.5.8": (
            ["6.4"],
            "deviation",
            "Комиссионный порядок подписания или отказа относится к той же приёмочной цепочке, но устанавливает иной срок и механизм; C14.",
        ),
        "9.3": (
            ["8.2"],
            "aligned",
            "Документальное подтверждение форс-мажора является процедурной частью того же механизма освобождения.",
        ),
        "11.1": (
            ["10.1"],
            "aligned",
            "Срок начинается с заключения и сохраняет прекращение при исчерпании цены; заполнение открытой даты матрицы не является дельтой.",
        ),
        "11.5": (
            ["10.2"],
            "deviation",
            "Односторонний отказ ограничен основаниями ГК РФ и процедурой статьи 95 Закона № 44-ФЗ вместо общего внесудебного права обеих сторон с уведомлением за 30 дней; C16.",
        ),
    }

    rows: list[dict[str, Any]] = []
    for original in raw["rows"]:
        contract_id = str(original["contract_id"]).strip().rstrip(".")
        if contract_id in excluded:
            continue
        row: dict[str, Any] = {
            "contract_id": contract_id,
            "matrix_ids": _matrix_ids(original.get("matrix_ids_raw") or ""),
            "status": original["status"],
            "adjudication_note": (
                original.get("cells", {}).get("Комментарий / расхождение") or ""
            ),
        }
        correction = corrections.get(contract_id)
        if correction:
            matrix_ids, status, note = correction
            row.update(
                matrix_ids=matrix_ids,
                status=status,
                adjudication_note=note,
            )
        rows.append(row)

    missing = ["2.6.3.2", "4.2.8", "5.1.7", "7.15"]
    payload = {
        "schema_version": "contract-review-adjudicated-gold.v3",
        "document_id": "KAVKAZ",
        "sources": {
            "contract": "inputs/kavkaz.txt",
            "matrix": "inputs/matrix.json",
            "legal_workbook": "gold_results/Kavkaz_analiz_formattirovannyi.xlsx",
        },
        "policy": {
            "orientation": "contract occurrences",
            "mapping": "Functional partial analogs are retained; common subject matter alone is insufficient.",
            "status": "Only casebook deltas are deviations.",
            "placeholder": "Filling an open matrix field is aligned; a contract blank against a filled matrix value is deviation.",
            "missing": "A partially covered row is not missing. A mandatory product row is excluded only when its product scope or its own local antecedent is inactive.",
            "operational_scope": "A colon-only parent that merely introduces operative children is excluded unless it states its own right, duty, trigger, actor, procedure, or consequence.",
        },
        "contract_groups": rows,
        "missing_matrix_ids": missing,
        "excluded_contract_ids": sorted(excluded),
        "counts": _counts(rows, missing),
    }
    _validate_source_ids(rows, missing, ROOT / "inputs" / "kavkaz.txt", MATRIX)
    return _write(target, payload)


def build_irkutsk() -> Path:
    source = ROOT / "benchmarks" / "irkutsk_adjudicated_gold.v6.json"
    target = ROOT / "benchmarks" / "irkutsk_adjudicated_gold.v7.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload["contract_groups"]
    missing = payload["missing_matrix_ids"]
    payload.update(
        schema_version="contract-review-adjudicated-gold.v7",
        counts=_counts(rows, missing),
    )
    payload["policy"]["verification"] = (
        "Version 7 revalidates source identifiers and preserves the lawyer-approved "
        "business discrepancy set from version 6."
    )
    _validate_source_ids(rows, missing, ROOT / "irkutsk.txt", MATRIX)
    return _write(target, payload)


def main() -> None:
    for target in (build_kaluga(), build_kuzbas(), build_kavkaz(), build_irkutsk()):
        payload = json.loads(target.read_text(encoding="utf-8"))
        print(target.relative_to(ROOT))
        print(json.dumps(payload["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
