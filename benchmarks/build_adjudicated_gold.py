from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _counts(rows: list[dict[str, Any]], missing: list[str]) -> dict[str, int]:
    statuses = Counter(row["status"] for row in rows)
    return {
        "operational_contract_groups": len(rows),
        "mapped_contract_groups": sum(bool(row["matrix_ids"]) for row in rows),
        "aligned": statuses["aligned"],
        "deviation": statuses["deviation"],
        "extra_in_contract": statuses["extra_in_contract"],
        "missing_in_contract": len(missing),
    }


def _validate_source_ids(
    rows: list[dict[str, Any]],
    missing: list[str],
    contract_path: Path,
    matrix_path: Path,
) -> None:
    contract_text = contract_path.read_text(encoding="utf-8")
    matrix_ids = {
        str(row["number"]).strip().rstrip(".")
        for row in json.loads(matrix_path.read_text(encoding="utf-8"))
    }
    required_occurrences = Counter(row["contract_id"] for row in rows)
    occurrence_keys: set[tuple[str, str | None]] = set()
    allowed_statuses = {"aligned", "deviation", "extra_in_contract"}
    for row in rows:
        occurrence_key = (row["contract_id"], row.get("source_locator"))
        if occurrence_key in occurrence_keys:
            raise ValueError(
                "gold contains more than one status row for source occurrence "
                f"{occurrence_key!r}"
            )
        occurrence_keys.add(occurrence_key)

        status = row.get("status")
        if not isinstance(status, str) or status not in allowed_statuses:
            raise ValueError(
                f"gold source occurrence {occurrence_key!r} has invalid status {status!r}"
            )
        matrix_row_ids = row.get("matrix_ids")
        if not isinstance(matrix_row_ids, list) or any(
            not isinstance(matrix_id, str) or not matrix_id
            for matrix_id in matrix_row_ids
        ):
            raise ValueError(
                f"gold source occurrence {occurrence_key!r} has invalid matrix_ids"
            )
        if len(matrix_row_ids) != len(set(matrix_row_ids)):
            raise ValueError(
                f"gold source occurrence {occurrence_key!r} has duplicate matrix_ids"
            )
        if status in {"aligned", "deviation"} and not matrix_row_ids:
            raise ValueError(
                f"gold source occurrence {occurrence_key!r} requires a mapping"
            )
        if status == "extra_in_contract" and matrix_row_ids:
            raise ValueError(
                f"gold source occurrence {occurrence_key!r} cannot be extra and mapped"
            )

    for contract_id, expected_count in required_occurrences.items():
        if re.fullmatch(r"\d+(?:\.\d+)*", contract_id):
            actual_count = len(
                re.findall(
                    rf"(?m)^{re.escape(contract_id)}(?:\.(?!\d)|(?=\s))",
                    contract_text,
                )
            )
        else:
            appendix = contract_id.split(" п.", 1)[0]
            short_appendix = re.fullmatch(r"Прил\.(\d+(?:\.\d+)*)", appendix)
            if short_appendix:
                appendix = f"Приложение №{short_appendix.group(1)}"
            compact_source = re.sub(r"\s+", "", contract_text)
            compact_appendix = re.sub(r"\s+", "", appendix)
            actual_count = compact_source.count(compact_appendix)
            expected_count = 1
        if actual_count < expected_count:
            raise ValueError(
                f"{contract_path.name}: {contract_id!r} occurs {actual_count}, "
                f"gold requires {expected_count}"
            )

    unknown_matrix_ids = sorted(
        {
            matrix_id
            for row in rows
            for matrix_id in row["matrix_ids"]
            if matrix_id not in matrix_ids
        }
    )
    if unknown_matrix_ids:
        raise ValueError(f"unknown matrix IDs: {unknown_matrix_ids}")
    unknown_missing = sorted(set(missing) - matrix_ids)
    if unknown_missing:
        raise ValueError(f"unknown missing matrix IDs: {unknown_missing}")
    mapped_ids = {matrix_id for row in rows for matrix_id in row["matrix_ids"]}
    collisions = sorted(mapped_ids & set(missing))
    if collisions:
        raise ValueError(f"matrix IDs both mapped and missing: {collisions}")


def build_irkutsk() -> Path:
    source = ROOT / "benchmarks" / "irkutsk_verified_gold.v2.json"
    target = ROOT / "benchmarks" / "irkutsk_adjudicated_gold.v6.json"
    payload = json.loads(source.read_text(encoding="utf-8"))

    corrections: dict[str, tuple[list[str], str, str]] = {
        "2.1": (["6.1", "6.7", "6.21"], "deviation", "Терминалы активны, но платёжное условие не предусматривает отдельную плату за их сервисное обслуживание; это целевой случай C06."),
        "2.3": (["3.3", "4.2.12", "5.1.4"], "deviation", "Неудержание комиссии совпадает с 3.3, но оплата комиссии по акту и счёту вместо матричного механизма образует отдельную дельту того же платёжного правоотношения."),
        "2.5": (["6.5", "6.6"], "deviation", "Оплата через семь рабочих дней расходится с матричным пятидневным сроком."),
        "2.6": (["6.5", "6.6"], "deviation", "Безналичное перечисление совпадает, но добавлены источник финансирования и момент оплаты в день списания средств."),
        "3.1": (["5.2.4", "5.2.7"], "aligned", "Срок оказания отдельных услуг не является аналогом срока действия всего договора, но круглосуточная периодичность частично соответствует круглосуточной авторизации и работоспособности оборудования."),
        "4.1.1": (["5.2.3"], "aligned", "Установка и подготовка терминалов к эксплуатации совпадают; бесплатность не делает этот установочный пункт аналогом обязанности оплатить сервис."),
        "4.1.4": (["5.1.6", "5.2.7"], "deviation", "Обязанность обеспечить исправное оборудование совпадает частично, но изменены сроки и порядок замены."),
        "4.1.6": (["6.3"], "deviation", "Есть аналог документооборота, но изменены состав документов и порядок их предоставления."),
        "4.1.8": (["4.1.2"], "deviation", "Право на консультации совпадает, но доступ ограничен рабочими днями и интервалом 08:00–16:30."),
        "4.2.3.1": (["5.1.8.1"], "aligned", "Основание прекращения авторизации совпадает; задолженность из 5.1.2 здесь не регулируется."),
        "4.2.3": (["5.1.8"], "deviation", "Право прекратить авторизацию совпадает, но отсутствует связанное право провести мероприятия по расторжению и полный перечень оснований."),
        "4.2.4": (["5.1.9", "5.1.10"], "deviation", "Проверка операций и обращение к эмитенту совпадают, но проверка предприятия по роду деятельности и мошенничеству раскрыта не полностью."),
        "4.2.5": (["5.1.11"], "deviation", "Тринадцатимесячный период совпадает, но не раскрыты дополнительные документы и порядок их запроса."),
        "4.2.6": (["5.1.14"], "deviation", "Право запрашивать разъяснения совпадает, но обязательный канал электронной почты не указан."),
        "4.2.7": (["4.2.19"], "deviation", "Общее право на содействие частично совпадает, но не раскрыты непротиводействие проверке и помощь в расследовании подозрительных операций."),
        "4.4.9": (["4.2.10", "4.2.19"], "deviation", "Передача документов частично покрывает содействие расследованию, но не раскрывает запрет противодействия и полное содействие."),
        "4.2.1.4": (["5.1.1.4"], "deviation", "Из защитного перечня удержаний исключены самостоятельные основания, предусмотренные матрицей."),
        "4.4.15": (["4.2.18", "4.2.20.6"], "deviation", "Обязанность возврата совпадает, но срок увеличен с пяти до семи рабочих дней; при расторжении не раскрыто снятие материалов Банка."),
        "4.3.1": (["4.1.1"], "deviation", "Право использовать рекламу совпадает, но канал согласования сужен до e-mail и добавлено условие о товарных знаках."),
        "4.3.4": (["6.3"], "deviation", "Право требовать отчётные документы соответствует встречной обязанности их предоставить, но состав и сроки не раскрыты."),
        "4.4.6": (["4.2.9"], "deviation", "Ответственность за сведения совпадает, но источником порядка названы договор и закон вместо банковского Порядка операций."),
        "4.4.14": (["4.2.20.5"], "deviation", "Немедленное уведомление совпадает, но обязательные каналы уведомления не указаны."),
        "6.1": (["11.3"], "deviation", "Обязанность не разглашать совпадает, но объект ограничен условиями договора вместо перечня сведений матрицы."),
        "7.2": (["7.2"], "deviation", "Шкала штрафов совпадает, но в том же пункте добавлена пеня за просрочку заказчика."),
        "5.7": (["6.4"], "deviation", "Добавлен момент принятия услуги по двустороннему акту в том же приёмочном правоотношении."),
        "7.5": (["7.6"], "deviation", "Шкала совпадает, но применение штрафа ограничено двумя перечисленными видами нарушений."),
        "7.6": (["7.4"], "aligned", "Удержание неустойки из оплаты относится к тому же правоотношению ответственности Банка, но не входит в целевой набор отклонений."),
        "7.11": (["7.2", "7.4"], "deviation", "Добавлен общий двадцатидневный срок уплаты неустойки по требованию."),
        "7.7": (["7.1"], "aligned", "Возмещение убытков независимо от неустойки является специальным последствием общей договорной ответственности."),
        "7.10": (["7.5"], "aligned", "Штраф Банка при расторжении за ненадлежащее исполнение относится к ответственности Банка за непросроченное нарушение."),
        "8.3": (["10.2"], "deviation", "Одностороннее расторжение упомянуто без обязательного тридцатидневного уведомления."),
        "8.4": ([], "extra_in_contract", "Пятидневный ответ на предложение о расторжении по соглашению не имеет аналога в матрице."),
        "8.5": (["10.3"], "deviation", "Сверка расчётов частично относится к последствиям расторжения, но отсутствует восемнадцатимесячный порядок."),
        "11.1": (["9.1"], "deviation", "Добавлены добровольное урегулирование и совместный протокол в том же спорном правоотношении."),
        "11.2": (["11.4"], "aligned", "Письменное дополнительное соглашение, подписанное сторонами, совпадает с формой изменений матрицы."),
        "11.3": (["9.1"], "aligned", "Пункт устанавливает претензионный порядок; срок ответа регулируется отдельно в 11.3.1."),
        "11.3.3": (["9.1"], "deviation", "Добавлено обязательное содержание денежной претензии — сумма и расчёт."),
        "11.3.4": (["9.1"], "deviation", "Добавлено требование приложить подтверждающие документы к претензии."),
        "11.4": (["9.3"], "deviation", "Общий судебный порядок конкретизирован исключительной договорной подсудностью по месту заказчика."),
        "5.4": (["6.4"], "deviation", "Срок возврата подписанного акта исчисляется двумя рабочими днями после получения вместо матричного срока возврата банковского документа."),
        "8.2": (["10.1", "10.3"], "deviation", "Пункт о сроке действия договора не содержит прекращения при исчерпании цены как альтернативного события."),
        "12.1": (["2.3", "2.3.1", "2.3.2", "2.3.4", "2.3.5"], "deviation", "Почта и e-mail покрыты частично, но факс и обязательный оригинал изменяют порядок; ДБО и E-invoicing отсутствуют."),
        "12.2": (["11.9", "11.10"], "deviation", "Перемена заказчика и переход обязательств связаны с передачей прав и правопреемством, но условия отличаются."),
        "12.3": (["11.9"], "deviation", "Матрица допускает передачу с согласия, договор устанавливает запрет с единственным исключением реорганизации."),
        "12.6": (["2.4", "11.7", "11.13"], "aligned", "Перечень приложений и признание их неотъемлемой частью прямо совпадают с приложениями матрицы; 11.7 и 11.13 являются ближайшими аналогами."),
        "6.2": (["11.3", "5.2.10"], "deviation", "Ответственность за конфиденциальность относится к защите сведений, но не устанавливает передачу данных в платёжную систему МИР."),
        "7.1": (["7.1"], "aligned", "Общая ответственность сторон совпадает, но не заменяет специальную ответственность предприятия за персонал."),
        "Прил.6": (["7.16"], "extra_in_contract", "Антикоррупционная обязанность совпадает, но приложение добавляет самостоятельные обязанности о конфликте интересов, ПОД/ФТ и уведомлениях."),
    }

    revised: list[dict[str, Any]] = []
    business_deviation_ids = {
        "2.1",
        "2.3",
        "2.5",
        "4.1.4",
        "4.2.1.2",
        "4.2.1.4",
        "4.4.4",
        "4.4.15",
        "5.4",
        "8.2",
        "9.2",
        "11.3.1",
        "12.1",
    }
    for original in payload["contract_groups"]:
        row = dict(original)
        correction = corrections.get(row["contract_id"])
        if correction:
            matrix_ids, status, note = correction
            row.update(matrix_ids=matrix_ids, status=status, adjudication_note=note)
        if row["contract_id"] == "Прил.6":
            row["contract_id"] = "1"
            row["source_locator"] = "Приложение №6, пункт 1"
        row["status"] = (
            "deviation"
            if row["contract_id"] in business_deviation_ids
            else "aligned"
            if row["matrix_ids"]
            else "extra_in_contract"
        )
        revised.append(row)

    missing = [
        "2.1",
        "4.2.14",
        "4.2.16",
        "4.2.16.1",
        "4.2.16.2",
        "4.2.16.3",
        "4.2.20.4",
        "4.2.20.7",
        "5.1.3",
        "5.1.1.6",
        "5.1.7",
        "6.20",
        "7.15",
    ]
    payload.update(
        schema_version="contract-review-adjudicated-gold.v6",
        contract_groups=revised,
        missing_matrix_ids=missing,
        counts=_counts(revised, missing),
        policy={
            "orientation": "contract occurrences",
            "mapping": "Soft mapping requires at least one semantic candidate in the adjudicated pool; numbering has no semantic weight.",
            "status": "Deviation is limited to the lawyer-approved discrepancy set; another mapped operative provision is aligned and an operative provision without an analog is extra.",
            "placeholder": "A blank is deviation when the corresponding mapped matrix requirement contains a concrete value, including an operative field in an attached form; a blank matching an open matrix field is aligned.",
            "missing": "A mandatory active matrix row is missing only when the whole contract has no full or partial semantic analog.",
        },
    )
    _validate_source_ids(
        revised, missing, ROOT / "irkutsk.txt", ROOT / "inputs" / "matrix.json"
    )
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _matrix_ids(raw: str) -> list[str]:
    if not raw or raw.strip().lower() in {"—", "нет аналога"}:
        return []
    return list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)+", raw)))


def build_altai() -> Path:
    source = ROOT / ".run" / "altai-new-gold.json"
    target = ROOT / "benchmarks" / "altai_adjudicated_gold.v3.json"
    raw = source.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    extracted = json.loads(raw.decode(encoding))

    excluded = {"1.1–1.32", "11.4", "11.9", "12 (Реквизиты)"}
    corrections: dict[str, tuple[list[str], str, str]] = {
        "2.1": (["3.1", "3.2", "3.3"], "aligned", "Предмет эквайринга и расчётов совпадает; аренда терминала не образует целевую дельту casebook."),
        "2.3": ([], "extra_in_contract", "Срок аренды оборудования не является сроком действия договора; совпадение календарной даты не создаёт аналог."),
        "3.2.3": (["4.2.20.2"], "deviation", "Из закрытого запрета передачи исключено разрешение передавать оборудование и материалы обслуживающей компании; C09."),
        "3.2.2": (["4.2.2", "4.2.9"], "aligned", "Общая обязанность соблюдать договор и инструктивные материалы частично покрывает порядок операций."),
        "3.2.4": (["4.2.20.3"], "aligned", "Доступ работников Банка функционально покрывает доступ исполнителя работ по терминалам; целевой дельты casebook нет."),
        "3.2.5": (["4.2.20.4", "5.2.3"], "aligned", "Приём терминалов по двустороннему акту частично покрывает механизм передачи и приёмки."),
        "3.2.6": (["4.2.20.5"], "deviation", "Немедленное уведомление предусмотрено, но назначенные матрицей способы направления исключены; C05."),
        "3.2.7": (["4.2.20.6"], "deviation", "Возврат в течение пяти рабочих дней предусмотрен, но самостоятельный триггер требования Банка исключён; C11."),
        "4.1.1": (["5.1.1", "5.1.1.2", "5.1.1.3", "5.1.1.4", "5.1.1.6", "4.2.13"], "deviation", "Из перечня удержаний исключены самостоятельные категории недействительных и оспоренных операций; C09."),
        "3.1": (["4.1.2", "2.3.7"], "aligned", "Право на консультацию через службу поддержки совпадает; конкретный номер не установлен и в тексте матрицы."),
        "4.1.3": (["5.1.7"], "aligned", "Передача Банком сведений в платёжную систему относится к 5.1.7; это не встречная обязанность Предприятия передать персональные данные в Банк по 4.2.16."),
        "4.2.1": (["5.2.3", "4.2.20.1", "5.2.5"], "deviation", "Прямой инструктаж частично покрывает обучение, но назначенный матрицей конкретный URL материалов исключён; C05."),
        "4.1.4": (["5.1.9", "5.1.10"], "aligned", "Проверка операций, предприятия и его ресурса относятся к одной fraud-control цепочке."),
        "4.1.6": (["5.1.16", "4.2.15", "5.1.11"], "aligned", "Право требовать документы и сведения частично покрывает обновление сведений и документы по операциям."),
        "4.2.4": (["5.2.3"], "aligned", "Передача принадлежностей и технической документации является частью подготовки терминала к эксплуатации."),
        "4.2.3": (["5.2.6"], "aligned", "Обеспечение терминалов информационными материалами совпадает."),
        "4.2.5": (["5.2.7"], "aligned", "Круглосуточная работоспособность и замена исправным терминалом в течение трёх рабочих дней совпадают."),
        "4.2.6": (["2.2", "3.2", "5.2.8"], "aligned", "Основной двухдневный срок перечисления и расчётный механизм совпадают; неповторённый fallback не является целевой дельтой casebook."),
        "4.2.7": (["5.2.8"], "aligned", "Дата получения расчётной информации после электронной сверки совпадает."),
        "5.1": (["6.1", "6.2", "6.3", "3.3"], "aligned", "Открытый тариф матрицы заполнен таким же открытым полем; ежемесячные документы частично покрыты."),
        "5.2": (["6.4"], "aligned", "Возврат подписанного УПД либо мотивированного отказа до 25-го числа совпадает."),
        "5.3": (["6.13"], "deviation", "Арендная плата заменяет отдельную плату за сервисное обслуживание активного терминала; C06."),
        "5.4": (["6.14", "6.15"], "aligned", "Направление УПД и счёта по оборудованию частично покрывает сервисную документарную цепочку."),
        "5.5": (["6.16"], "aligned", "Возврат УПД по арендной плате до 25-го числа совпадает."),
        "5.6": (["6.17", "6.18", "6.5", "6.6"], "deviation", "Оплата установлена в семь рабочих дней вместо пяти и зависит от полного комплекта документов; C04."),
        "5.7": (["6.20"], "aligned", "При возврате и реверсе новая плата не взимается, первоначальная не возвращается."),
        "5.8": (["6.21"], "aligned", "Цена договора и соответствующее поле матрицы оставлены открытыми."),
        "5.9": (["3.3", "6.5"], "aligned", "Оплата фактически оказанных и принятых услуг не образует целевую дельту casebook."),
        "6.2": ([], "extra_in_contract", "Двустороннее изменение или расторжение по соглашению не является аналогом одностороннего отказа."),
        "6.3": (["11.4"], "deviation", "Письменная форма совпадает, но отсутствует исключение для документов, изменяемых публикацией на сайте банка."),
        "6.4": ([], "extra_in_contract", "Взаиморасчёты при расторжении по взаимной договорённости не являются аналогом расчётов после одностороннего расторжения Банком."),
        "6.5": (["5.1.13", "4.2.14"], "deviation", "Вместо публикации изменений Банком на сайте установлено взаимное уведомление в течение пяти рабочих дней; C05."),
        "7.2": (["7.2"], "deviation", "Фиксированный штраф 1000 рублей заменяет шкалу, зависящую от цены договора."),
        "7.3": (["7.3"], "aligned", "Предел общей суммы штрафов предприятия равен цене договора."),
        "7.4": (["7.4"], "aligned", "Формула пени и уменьшение базы на исполненный объём совпадают."),
        "7.5": (["7.5"], "deviation", "Фиксированные 10 процентов заменяют шкалу штрафа по цене договора."),
        "7.6": (["7.6"], "deviation", "Фиксированные 1000 рублей заменяют шкалу штрафа за нестоимостные нарушения."),
        "7.7": (["7.7"], "aligned", "Предел общей суммы штрафов банка равен цене договора."),
        "7.8": (["7.9"], "aligned", "Освобождение банка от ответственности за задержки не по его вине совпадает."),
        "7.9": (["7.10"], "aligned", "Освобождение банка за действия третьих лиц совпадает."),
        "7.10": (["7.11"], "aligned", "Освобождение при расследовании подозрительных операций совпадает."),
        "7.11": (["7.15"], "aligned", "Ответственность предприятия за действия персонала совпадает."),
        "8.2": (["8.2"], "deviation", "Срок уведомления совпадает, но назначенный матрицей договорный способ направления исключён; C05."),
        "9.2 (ответ)": (["9.1", "2.3.1", "2.3.4"], "aligned", "Ответ по e-mail и заказным письмом относится к претензионному и коммуникационному механизму без целевой дельты этого пункта."),
        "9.1 (суд)": (["9.3"], "aligned", "Договорная подсудность конкретизирует судебное разрешение спора; casebook не относит выбор суда к целевой дельте."),
        "9.2 (суд)": (["9.3"], "aligned", "Договорная подсудность конкретизирует судебное разрешение спора; casebook не относит выбор суда к целевой дельте."),
        "10.1": (["7.16"], "aligned", "Антикоррупционный запрет совпадает по юридическому механизму."),
        "10.2": (["7.16"], "aligned", "Уведомление о подозрении является процедурной стадией общей антикоррупционной цепочки."),
        "10.3": (["7.16"], "aligned", "Рассмотрение уведомления является процедурной стадией общей антикоррупционной цепочки."),
        "10.4": (["7.16"], "aligned", "Разбирательство и защита уведомившей стороны являются стадиями общей антикоррупционной цепочки."),
        "10.5": (["7.16"], "aligned", "Повторённое разбирательство является стадией общей антикоррупционной цепочки."),
        "11.1": (["11.1"], "aligned", "Иерархия договора, законодательства и правил платёжных систем совпадает."),
        "11.2": (["11.2", "4.2.7"], "aligned", "Конфиденциальность реквизитов карт частично покрывает запрет использовать их для иных целей."),
        "11.3": (["11.3", "5.1.7"], "deviation", "Абсолютное требование согласия сторон исключает передачу данных в ПС МИР, прямо сохранённую матрицей; C08."),
        "11.5": (["2.4", "11.7"], "aligned", "Неотъемлемость приложений совпадает с матрицей."),
        "11.6": (["11.6", "11.8", "4.2.15"], "aligned", "Заверение о законности деятельности совпадает; специальные обязанности обновления оцениваются отдельно."),
        "11.7": (["11.9"], "aligned", "Передача прав и обязанностей требует согласования; реорганизация является исключением."),
        "11.8": (["11.10"], "aligned", "Последствия реорганизации и ликвидации совпадают."),
    }

    duplicate_labels = {
        "9.1 (претензия)": ("9.1", "раздел 9, первое вхождение — претензионный порядок"),
        "9.2 (ответ)": ("9.2", "раздел 9, первое вхождение — ответ на претензию"),
        "9.1 (суд)": ("9.1", "раздел 9, второе вхождение — передача спора в суд"),
        "9.2 (суд)": ("9.2", "раздел 9, второе вхождение — подсудность"),
    }
    appendix_labels = {
        "Прил.1 (ТЗ)": "Приложение №1",
        "Прил.2 (Акт)": "Приложение №2",
        "Прил.3 (Спецификация)": "Приложение №3",
        "Прил.4 (Заявление)": "Приложение №4",
        "Прил.4.1 (Инф. о ТСТ)": "Приложение №4.1",
    }

    rows: list[dict[str, Any]] = []
    for source_row in extracted["rows"]:
        raw_id = source_row["contract_id"]
        if raw_id in excluded or raw_id.startswith("Прил."):
            continue
        contract_id, locator = duplicate_labels.get(
            raw_id, (appendix_labels.get(raw_id, raw_id), None)
        )
        if raw_id == "3.1":
            # The workbook dropped the final component. The operative source
            # provision is 3.1.1; 3.1 is only the parent heading.
            contract_id = "3.1.1"
        row: dict[str, Any] = {
            "contract_id": contract_id,
            "matrix_ids": _matrix_ids(source_row.get("matrix_ids_raw", "")),
            "status": source_row["status"],
        }
        if locator:
            row["source_locator"] = locator
        correction = corrections.get(raw_id)
        if correction:
            matrix_ids, status, note = correction
            row.update(matrix_ids=matrix_ids, status=status, adjudication_note=note)
        rows.append(row)

    appendix_rows = [
        ("Приложение №1", "плата за расчёты", ["6.1"], "aligned"),
        ("Приложение №1", "перечисление сумм", ["5.2.8", "2.2"], "aligned"),
        ("Приложение №1", "оплата комиссии", ["6.5", "6.6"], "aligned"),
        ("Приложение №1", "электронная авторизация", ["5.2.4"], "aligned"),
        ("Приложение №1", "доставка и пуско-наладка", ["5.2.3"], "aligned"),
        ("Приложение №1", "характеристики смарт-терминала", [], "extra_in_contract"),
        ("Приложение №1", "доступ в интернет и обмен с ОФД", [], "extra_in_contract"),
        ("Приложение №1", "безопасность услуги", [], "extra_in_contract"),
        ("Приложение №1", "качество, комплектность и акт передачи", ["5.2.3", "4.2.20.4", "5.2.7"], "aligned"),
        ("Приложение №1", "требования к результатам", [], "extra_in_contract"),
        ("Приложение №1", "место оказания услуг", [], "extra_in_contract"),
        ("Приложение №1", "условия оказания услуг", ["5.2.3"], "aligned"),
        ("Приложение №1", "сроки оказания услуг и аренды", ["10.1"], "deviation"),
        ("1", "Приложение №2, пункт 1 — передача и принятие ККТ", ["5.2.3", "4.2.20.4"], "aligned"),
        ("2", "Приложение №2, пункт 2 — состояние, документы и отсутствие фискальных накопителей", ["5.2.3"], "aligned"),
        ("3", "Приложение №2, пункт 3 — полнота информации и осмотр", ["4.2.20.4"], "aligned"),
        ("4", "Приложение №2, пункт 4 — два экземпляра акта", ["4.2.20.4"], "aligned"),
        ("1.1", "Приложение №3, оказание услуг в 2023 году", ["6.1", "6.13"], "deviation"),
        ("1.2", "Приложение №3, оказание услуг в 2024 году", ["6.1", "6.13"], "deviation"),
        ("Приложение №3", "строка ИТОГО — начальная максимальная цена", ["6.21"], "aligned"),
        ("1.1", "Приложение №4, раздел 2 — полнота сведений", ["2.1", "4.2.15"], "aligned"),
        ("1.2", "Приложение №4, раздел 2 — проверка сведений", ["5.1.10"], "aligned"),
        ("1.3", "Приложение №4, раздел 2 — заранее данный акцепт", ["5.1.3"], "aligned"),
        ("Приложение №4.1", "форма информации о ТСТ", ["2.1"], "deviation"),
    ]
    rows.extend(
        {
            "contract_id": contract_id,
            "source_locator": locator,
            "matrix_ids": matrix_ids,
            "status": status,
        }
        for contract_id, locator, matrix_ids, status in appendix_rows
    )

    # The original workbook's matrix-only sheet was not reliable enough to
    # reuse mechanically. These are the mandatory active requirements with no
    # full or partial analog after the clause-by-clause adjudication.
    missing = [
        "2.3.2",
        "2.3.5",
        "4.2.5",
        "4.2.6",
        "4.2.7",
        "4.2.8",
        "4.2.10",
        "4.2.11",
        "4.2.16",
        "4.2.16.1",
        "4.2.16.2",
        "4.2.16.3",
        "4.2.18",
        "4.2.19",
        "5.1.1.1",
        "5.1.1.4",
        "5.1.1.5",
        "5.1.2",
        "5.1.4",
        "5.1.5",
        "5.1.11",
        "10.2",
        "10.3",
    ]
    mapped_ids = {matrix_id for row in rows for matrix_id in row["matrix_ids"]}
    missing = [matrix_id for matrix_id in missing if matrix_id not in mapped_ids]
    payload = {
        "schema_version": "contract-review-adjudicated-gold.v2",
        "document_id": "ALTAI",
        "sources": {
            "contract": "altai.txt",
            "matrix": "inputs/matrix.json",
            "legal_workbook": "gold_results/altai_transport_analysis_v1.xlsx",
            "audited_extract": ".run/altai-new-gold.json",
        },
        "policy": {
            "orientation": "contract occurrences",
            "mapping": "Soft mapping accepts any adjudicated semantic candidate; numbering is ignored.",
            "status": "Deviation is limited to the lawyer-approved discrepancy set; another mapped operative provision is aligned and an operative provision without an analog is extra.",
            "placeholder": "A blank is deviation when the corresponding mapped matrix requirement contains a concrete value, including an operative field in an attached form; a blank matching an open matrix field is aligned.",
            "scope": "Trade acquiring and POS are active. A single optional QR field does not activate QR requirements.",
        },
        "contract_groups": rows,
        "missing_matrix_ids": missing,
        "excluded_contract_ids": sorted(excluded),
        "counts": _counts(rows, missing),
    }
    _validate_source_ids(
        rows, missing, ROOT / "altai.txt", ROOT / "inputs" / "matrix.json"
    )
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def build_kuzbas() -> Path:
    source = ROOT / "benchmarks" / "kuzbas_verified_gold.v1.json"
    target = ROOT / "benchmarks" / "kuzbas_adjudicated_gold.v2.json"
    payload = json.loads(source.read_text(encoding="utf-8"))

    corrections: dict[str, tuple[list[str], str, str]] = {
        "2.3.6": (["2.3.6"], "aligned", "Точный договорный аналог оценивается независимо от lot-selector матрицы."),
        "3.1": (["3.1"], "deviation", "QR-scope активирован прямыми обязанностями предоставить QR-код и QR-API, но из предметного перечня исключены предусмотренные матрицей QR-способы оплаты; C09."),
        "4.1.1": (["4.1.1"], "deviation", "QR-scope активирован, но из права упоминать способы оплаты в информационных материалах исключён QR-код; C09."),
        "4.1.3": (["4.1.3"], "deviation", "Сильный аналог права использовать полученные от Банка средства оплаты не называет QR-код и исключает предусмотренные матрицей QR-способы; C09."),
        "4.2.1": (["4.2.1", "6.7", "6.13"], "deviation", "Активные терминалы используются, но обязанность оплаты не устанавливает отдельную сервисную плату; C06."),
        "4.2.4": (["4.2.4"], "deviation", "QR-scope активирован, но из обязанности размещать материалы исключены предусмотренные матрицей QR-способы оплаты; C09."),
        "4.2.12": (["4.2.12", "5.1.3", "5.1.4"], "deviation", "Акцепт требования относится к той же цепочке взыскания, но не сохраняет списание без дополнительного распоряжения; C03."),
        "4.2.17": (["4.2.17"], "deviation", "Точный аналог PCI DSS не содержит назначенного матрицей адреса направления результатов; C05."),
        "4.2.20.1": (["4.2.20.1"], "deviation", "Матрица назначает конкретный URL инструктажа, а договор оставляет только неопределённый официальный сайт Банка; C05."),
        "5.2.3": (["5.2.5"], "deviation", "Матрица назначает конкретный URL обучающих материалов, а договор оставляет только неопределённый официальный сайт Банка; C05."),
        "5.1.1.1": (["5.1.1.1"], "deviation", "При активном QR из закрытого перечня недействительных операций исключены прямо предусмотренные матрицей SberPay/Плати QR; C09."),
        "5.1.1.4": (["5.1.1.4"], "deviation", "При активном QR общий термин «Платёжные решения» не сохраняет прямо предусмотренные матрицей SberPay/Плати QR в перечне оспариваемых операций; C09."),
        "5.2.6": (["5.2.8"], "deviation", "При активном QR из перечня каналов передачи расчётной информации исключены Ресурс и QR-код; C09."),
        "5.2.7": (["5.2.9", "2.6.1.1", "2.6.3.1"], "aligned", "Предоставление партнёрского QR-кода вместе с установкой терминалов частично покрывает способы подключения QR."),
        "5.2.10": (["5.2.12", "2.6.2.1", "2.6.2.2"], "aligned", "Предоставление QR-API частично покрывает API- и vendor-механизмы подключения QR."),
        "6.1": (["6.1", "6.7", "6.13"], "deviation", "Платёжный раздел устанавливает только плату за расчёты по операциям и не устанавливает отдельную плату за активное сервисное обслуживание терминалов; C06."),
        "6.2": (["6.2", "6.8", "6.14"], "aligned", "Общий срок направления УПД частично покрывает специальную сервисную документарную цепочку."),
        "6.3": (["6.3", "6.9", "6.15"], "aligned", "Общий срок направления акта и счёта частично покрывает специальную сервисную документарную цепочку."),
        "6.4": (["6.4", "6.5", "6.10", "6.11", "6.16", "6.17"], "deviation", "Оплата после подписания УПД предусмотрена, но обязанность вернуть документ или отказ к установленной дате отсутствует; C14."),
        "6.5": (["6.6", "6.12", "6.18"], "aligned", "Оплата после акта и счёта частично покрывает специальную сервисную платёжную цепочку."),
        "7.2": (["7.4"], "aligned", "Письменное требование является процедурным элементом соответствующей неустойки."),
        "7.3": (["7.7"], "aligned", "Лимит ответственности Банка совпадает с лимитом ответственности Банка, а не Предприятия."),
        "7.5": (["7.2", "7.3"], "deviation", "Ответственность Предприятия за непросроченное нарушение предусмотрена, но отсутствуют требуемые размер штрафа и предел; C07."),
        "7.5.1": (["7.1"], "aligned", "Пеня Предприятия за просрочку относится к общей ответственности, но не заменяет фиксированный штраф за иной класс нарушения."),
        "7.7": (["8.1"], "aligned", "Освобождение при непреодолимой силе является прямым частичным аналогом общего форс-мажорного освобождения."),
        "7.8": (["7.1"], "aligned", "Сохранение обязанности после санкции относится к общему механизму договорной ответственности."),
        "7.9": (["7.8"], "deviation", "При активном QR из перечня способов оплаты в исключении ответственности Банка исключены SberPay/Плати QR; C09."),
        "7.16": (["7.17"], "aligned", "Точный договорный аналог повторяющихся платежей оценивается независимо от product-selector матрицы."),
        "11.11": (["11.11"], "deviation", "Матрица назначает конкретный адрес официального сайта Банка, а договор оставляет только неопределённое наименование сайта; C05."),
    }

    rows: list[dict[str, Any]] = []
    for original in payload["contract_groups"]:
        row = dict(original)
        correction = corrections.get(row["contract_id"])
        if correction:
            matrix_ids, status, note = correction
            row.update(
                matrix_ids=matrix_ids,
                status=status,
                adjudication_note=note,
            )
        rows.append(row)

    # The former matrix-only list duplicated partially covered payment,
    # service-document and QR connection chains. QR is active, but the
    # independent right to disconnect it has no contract analog.
    missing: list[str] = ["2.6.3.2"]
    payload.update(
        schema_version="contract-review-adjudicated-gold.v2",
        document_id="KUZBAS",
        sources={
            "contract": "kyzbas.txt",
            "matrix": "inputs/matrix.json",
            "legal_workbook": "gold_results/kuzbas_stomat_analysis_v1.xlsx",
            "verified_gold": "benchmarks/kuzbas_verified_gold.v1.json",
        },
        policy={
            "orientation": "contract occurrences",
            "mapping": "Any full or partial semantic analog prevents extra and prevents simultaneous missing for that matrix row.",
            "status": "Only casebook deltas are deviations; mapped provisions without a target delta are aligned.",
            "scope": "Selectors restrict only matrix-side missing. An exact or strong contract-side analog is always evaluated.",
        },
        contract_groups=rows,
        missing_matrix_ids=missing,
        counts=_counts(rows, missing),
    )
    _validate_source_ids(
        rows, missing, ROOT / "kyzbas.txt", ROOT / "inputs" / "matrix.json"
    )
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> None:
    for target in (build_irkutsk(), build_altai(), build_kuzbas()):
        payload = json.loads(target.read_text(encoding="utf-8"))
        print(target)
        print(json.dumps(payload["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
