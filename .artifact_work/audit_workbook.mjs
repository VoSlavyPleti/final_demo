import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve("..");
const inputPath = path.join(root, "gold_results", "KAVKAZ.xlsx");
const outDir = path.join(root, ".artifact_work", "renders");
await fs.mkdir(outDir, { recursive: true });

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 6000,
  tableMaxRows: 8,
  tableMaxCols: 8,
  tableMaxCellChars: 120,
});
console.log(summary.ndjson);

for (const sheetName of ["🔁 Контракт ↔ Матрица", "Только в матрице"]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 0.8,
    format: "png",
  });
  const safeName = sheetName === "🔁 Контракт ↔ Матрица" ? "contract-map" : "matrix-only";
  await fs.writeFile(
    path.join(outDir, `${safeName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}
