import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve("..");
const sourcePath = path.join(root, "gold_results", "KAVKAZ.xlsx");
const matrixPath = path.join(root, "inputs", "matrix.json");
const outputPath = path.join(root, ".artifact_work", "KAVKAZ.corrected.xlsx");
const renderDir = path.join(root, ".artifact_work", "corrected-renders");

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
const contractSheet = workbook.worksheets.getItem("🔁 Контракт ↔ Матрица");
const matrixSheet = workbook.worksheets.getItem("Только в матрице");
const matrixRows = JSON.parse(await fs.readFile(matrixPath, "utf8"));

const normalizeId = (value) => String(value ?? "").trim().replace(/\.$/, "");
const parseMatrixIds = (value) => {
  const text = String(value ?? "");
  if (!text || text === "—" || text === "-") return [];
  return [...text.matchAll(/\d+(?:\.\d+)+\.?/g)].map((match) =>
    normalizeId(match[0]),
  );
};

const contractValues = contractSheet.getRange("A1:E149").values;
const contractUpdates = {
  "1.1": {
    status: "⚠️ Расхождение",
    candidates: "2.4, 3.1, 3.2, 3.3, 11.7, 11.13",
    comment:
      "Предмет, обязанность оплаты и неотъемлемость приложений покрыты матрицей. Состав приложений отличается от п. 11.13 матрицы: Заявление и Информация о ТСТ включены в договор, но отдельные формы Акта о перечислении сумм операций и описания тарифа/сервиса Смарт-терминалов как приложения отсутствуют.",
  },
  "1.3": {
    status: "⚠️ Расхождение",
    candidates: "3.1, 4.2.5",
    comment:
      "Связанное Техническое задание предусматривает VISA и MasterCard наряду с «МИР», что отличается от допустимого матрицей состава платежных систем. Кроме того, договор не воспроизводит запреты п. 4.2.5 матрицы на выдачу наличных и прием более двух различных карт.",
  },
  "3.1": {
    status: "✅ Соответствует",
    candidates: "6.21",
    comment:
      "Цена договора и валюта указаны; заполнение предусмотренного матрицей ценового поля конкретным значением само по себе не является отклонением.",
  },
  "3.1.1": {
    status: "⚠️ Расхождение",
    candidates: "6.1, 11.7",
    comment:
      "Пункт отсылает к Спецификации как неотъемлемому приложению, однако в Спецификации не заполнен размер комиссии/цена услуги в процентах, требуемый п. 6.1 матрицы.",
  },
  "3.2": {
    status: "⚠️ Расхождение",
    candidates: "6.1",
    comment:
      "Механизм вознаграждения как процента от суммы операции соответствует матрице, но конкретный процент комиссии в договоре и Спецификации не заполнен.",
  },
  "4.1": {
    status: "❌ Нет аналога",
    candidates: "—",
    comment:
      "Период оказания услуг и прекращение оказания при исчерпании предельной цены являются самостоятельным закупочным условием. Это не аналог срока действия договора из п. 10.1 матрицы.",
  },
  "4.4": {
    status: "⚠️ Расхождение",
    candidates: "3.3, 4.2.1, 5.1.13, 6.5",
    comment:
      "Оплата перечислением Заказчиком на счет Исполнителя соответствует основному механизму п. 6.5 матрицы. Расхождение создают дополнительная обязанность Банка письменно уведомить об изменении счета в течение двух дней и возложение на Банк всех рисков при неуведомлении; матрица предусматривает уведомление путем публикации на сайте без такого срока и санкции.",
  },
  "5.1.4": {
    status: "⚠️ Расхождение",
    candidates: "5.2.7",
    comment:
      "Основной пункт воспроизводит трехдневный срок замены терминала из матрицы, но связанное Техническое задание дополнительно устанавливает двухдневное время реакции на замену, ремонт и восстановление POS-терминала.",
  },
  "5.1.9": {
    status: "✅ Соответствует",
    candidates: "5.2.10",
    comment:
      "Обязанность Исполнителя обрабатывать полученные от Заказчика персональные данные и обеспечивать их конфиденциальность и защиту практически дословно соответствует п. 5.2.10 матрицы.",
  },
  "5.1.10": {
    status: "✅ Соответствует",
    candidates: "3.2",
    comment:
      "Пункт содержит общее обязательство Исполнителя перечислять Заказчику суммы операций и соответствует общей норме п. 3.2 матрицы; специальные сроки и валюта урегулированы отдельно в п. 5.1.8 договора.",
  },
  "5.2.5": {
    status: "⚠️ Расхождение",
    candidates: "5.1.9, 5.1.11",
    comment:
      "Право запрашивать документы для анализа спорных операций соответствует п. 5.1.11 и частично покрывает дополнительные проверки по п. 5.1.9. В договоре отсутствует предусмотренное п. 5.1.9 право обращаться в банк-эмитент для проверки правомерности операции.",
  },
  "5.2.9": {
    status: "⚠️ Расхождение",
    candidates:
      "5.1.2, 5.1.8, 5.1.8.1, 5.1.8.2, 5.1.8.3, 5.1.8.6, 5.1.8.7, 5.1.8.8, 5.1.8.9, 5.1.8.10, 5.1.8.11, 5.1.8.12, 5.1.8.13",
    comment:
      "Перечень оснований в основном соответствует матрице, однако договор предусматривает приостановление, а не прекращение авторизации. Общее основание «нарушение условий» охватывает задолженность, но договор не закрепляет специальный режим приостановления до ее полного погашения из п. 5.1.2 матрицы.",
  },
  "5.2.10.1": {
    status: "✅ Соответствует",
    candidates: "5.1.1.1, 5.1.5",
    comment:
      "Удержание сумм недействительных операций, включая операции, проведенные с нарушением договора, покрывает как перечень п. 5.1.1.1, так и запрет возмещать нарушающие договор операции из п. 5.1.5 матрицы.",
  },
  "5.3.2": {
    status: "✅ Соответствует",
    candidates: "3.3, 4.2.1, 6.5",
    comment:
      "Общая обязанность Заказчика оплатить услуги покрывает применимые требования об оплате. Пункт 6.6 матрицы к договору по 44-ФЗ неприменим.",
  },
  "5.3.11": {
    status: "⚠️ Расхождение",
    candidates: "4.2.10, 11.2, 11.3",
    comment:
      "Срок хранения и передачи документов полностью соответствует п. 4.2.10. При этом обязанность хранить документы в недоступном месте лишь частично покрывает конфиденциальность по пп. 11.2–11.3: договор не устанавливает полный состав защищаемой информации, общий запрет разглашения и условие о согласии сторон.",
  },
  "5.3.17": {
    status: "⚠️ Расхождение",
    candidates: "4.2.19, 5.1.10",
    comment:
      "Обязанность Заказчика не препятствовать проверкам и содействовать расследованию соответствует п. 4.2.19. Право Банка проводить проверки из п. 5.1.10 покрывается только через обратную обязанность Заказчика и прямо в договоре не закреплено.",
  },
  "5.4.2": {
    status: "✅ Соответствует",
    candidates: "6.4",
    comment:
      "Право Заказчика отказать в приемке услуг ненадлежащего качества соответствует предусмотренной п. 6.4 матрицы возможности представить мотивированный отказ от подписания документа о приемке.",
  },
  "5.4.5": {
    status: "❌ Нет аналога",
    candidates: "—",
    comment:
      "Право Заказчика ссылаться в рекламе на прием карт при предварительном согласовании с Исполнителем не имеет применимого аналога: п. 4.1.1 матрицы отнесен селектором к QR, который для данного договора не подтвержден.",
  },
  "5.10": {
    status: "⚠️ Расхождение",
    candidates: "4.2.20.1, 11.11",
    comment:
      "В договоре не заполнен адрес сайта с инструктажем. Кроме того, отсутствует предусмотренный п. 11.11 матрицы общий режим обязательности размещенных Банком инструктивных материалов и момент вступления их в силу.",
  },
  "7.1": {
    status: "⚠️ Расхождение",
    candidates: "7.1, 7.3, 7.7, 7.15",
    comment:
      "Общая ответственность сторон и предельные суммы штрафов соответствуют пп. 7.1, 7.3 и 7.7. Договор не закрепляет отдельно требование п. 7.15 о полной ответственности Предприятия за действия персонала, нарушающие договор и инструкции Банка.",
  },
  "8.3.2": {
    status: "✅ Соответствует",
    candidates: "2.4, 6.2",
    comment:
      "Документы, приложенные к документу о приемке, признаются его неотъемлемой частью и вместе с п. 8.3.1 договора покрывают состав расчетного документа. Пункт 6.3 матрицы относится только к 223-ФЗ и неприменим.",
  },
  "11.1": {
    status: "⚠️ Расхождение",
    candidates: "10.1",
    comment:
      "Конкретная дата заполняет предусмотренное матрицей поле и сама по себе не является отклонением. Расхождение состоит в том, что договор сохраняет до полного исполнения все возникшие обязательства, тогда как матрица после окончания срока прямо сохраняет только ответственность за нарушения.",
  },
  "11.2": {
    status: "⚠️ Расхождение",
    candidates: "5.1.12, 11.4",
    comment:
      "Письменная форма двусторонних изменений соответствует п. 11.4. Одновременно договор не сохраняет право Банка по п. 5.1.12 в одностороннем порядке изменять документы по ссылкам через публикацию на сайте и дополнительно вводит двухдневную обязанность уведомлять об изменении адреса или названия.",
  },
};

for (let rowIndex = 2; rowIndex < contractValues.length; rowIndex += 1) {
  const row = contractValues[rowIndex];
  const contractId = normalizeId(row[0]);
  const update = contractUpdates[contractId];
  if (update) {
    row[2] = update.status;
    row[3] = update.candidates;
    row[4] = update.comment;
  }
}

contractValues[0][0] =
  "ЧАСТЬ 1. ПУНКТЫ КОНТРАКТА: СОПОСТАВЛЕНИЕ С МАТРИЦЕЙ БАНКА (147 пунктов)";
contractSheet.getRange("A1:E149").values = contractValues;

const statusFills = {
  "✅ Соответствует": "#E2EFDA",
  "⚠️ Расхождение": "#FFE699",
  "❌ Нет аналога": "#FFC7CE",
};
for (let rowIndex = 2; rowIndex < contractValues.length; rowIndex += 1) {
  const status = String(contractValues[rowIndex][2] ?? "");
  const fill = statusFills[status];
  if (fill) {
    contractSheet.getRange(`A${rowIndex + 1}:E${rowIndex + 1}`).format.fill = fill;
  }
}

const existingMatrixValues = matrixSheet.getRange("A1:D94").values;
const existingResidual = new Map();
for (let rowIndex = 4; rowIndex < existingMatrixValues.length; rowIndex += 1) {
  const row = existingMatrixValues[rowIndex];
  const matrixId = normalizeId(row[0]);
  if (matrixId) {
    existingResidual.set(matrixId, {
      category: row[2],
      reason: row[3],
    });
  }
}

const newlyMapped = new Set([
  "5.1.2",
  "5.1.5",
  "5.1.9",
  "5.1.12",
  "11.3",
  "11.11",
  "11.13",
]);
for (const matrixId of newlyMapped) existingResidual.delete(matrixId);

existingResidual.set("4.1.1", {
  category: "not_applicable",
  reason:
    "Селектор payment_method=[qr] не совпадает с подтвержденным профилем договора: использование QR как способа оплаты договором не установлено.",
});
existingResidual.set("4.2.12", {
  category: "optional_absent",
  reason:
    "Пункт имеет required_type=optional. Договор использует иной прямой механизм оплаты перечислением Заказчиком по п. 6.5 матрицы; акцепт платежных требований/счетов Банка отдельно не предусмотрен.",
});
existingResidual.set("6.3", {
  category: "not_applicable",
  reason:
    "Селектор only_for_lot=[fz_223] не совпадает с режимом закупки договора: 44-ФЗ.",
});
existingResidual.set("6.6", {
  category: "not_applicable",
  reason:
    "Селектор only_for_lot=[fz_223] не совпадает с режимом закупки договора: 44-ФЗ.",
});

const residualRows = [];
for (const matrixRow of matrixRows) {
  const matrixId = normalizeId(matrixRow.number);
  const residual = existingResidual.get(matrixId);
  if (!residual) continue;
  residualRows.push([
    matrixId,
    matrixRow.text,
    residual.category,
    residual.reason,
  ]);
}

const mappedIds = new Set();
for (let rowIndex = 2; rowIndex < contractValues.length; rowIndex += 1) {
  for (const matrixId of parseMatrixIds(contractValues[rowIndex][3])) {
    mappedIds.add(matrixId);
  }
}
const residualIds = new Set(residualRows.map((row) => normalizeId(row[0])));
const matrixSourceIds = new Set(matrixRows.map((row) => normalizeId(row.number)));
const uncovered = [...matrixSourceIds].filter(
  (matrixId) => !mappedIds.has(matrixId) && !residualIds.has(matrixId),
);
const overlap = [...mappedIds].filter((matrixId) => residualIds.has(matrixId));
if (uncovered.length || overlap.length) {
  throw new Error(
    `Coverage failure: uncovered=${JSON.stringify(uncovered)}, overlap=${JSON.stringify(overlap)}`,
  );
}
if (mappedIds.size + residualIds.size !== matrixRows.length) {
  throw new Error(
    `Coverage count mismatch: mapped=${mappedIds.size}, residual=${residualIds.size}, source=${matrixRows.length}`,
  );
}

const matrixOutput = Array.from({ length: 94 }, () => [null, null, null, null]);
matrixOutput[0][0] =
  `ОСТАТОЧНЫЕ ПУНКТЫ МАТРИЦЫ (${residualRows.length}); MAPPED ${mappedIds.size} + RESIDUAL ${residualIds.size} = ${matrixRows.length}`;
matrixOutput[1] = [
  "Пункт матрицы",
  "Краткое содержание (матрица)",
  "Expected coverage category",
  "Обоснование категории",
];
for (let index = 0; index < residualRows.length; index += 1) {
  matrixOutput[index + 4] = residualRows[index];
}
matrixSheet.getRange("A1:D94").values = matrixOutput;

await fs.mkdir(renderDir, { recursive: true });
for (const [sheetName, fileName] of [
  ["🔁 Контракт ↔ Матрица", "contract-map.png"],
  ["Только в матрице", "matrix-only.png"],
]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 0.8,
    format: "png",
  });
  await fs.writeFile(
    path.join(renderDir, fileName),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);
console.log(
  JSON.stringify(
    {
      contractRows: contractValues.length - 2,
      mappedMatrixIds: mappedIds.size,
      residualMatrixIds: residualIds.size,
      matrixRows: matrixRows.length,
      residualCategories: residualRows.reduce((counts, row) => {
        counts[row[2]] = (counts[row[2]] ?? 0) + 1;
        return counts;
      }, {}),
      contractStatuses: contractValues.slice(2).reduce((counts, row) => {
        counts[row[2]] = (counts[row[2]] ?? 0) + 1;
        return counts;
      }, {}),
    },
    null,
    2,
  ),
);

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
console.log(`Saved ${outputPath}`);
