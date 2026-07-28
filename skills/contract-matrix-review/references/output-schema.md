# Формат результата `contract-matrix-map.v4`

Сохранить единственный публикуемый юридический UTF-8 JSON. Служебные артефакты
harness не являются частью этого формата.

```json
{
  "schema_version": "contract-matrix-map.v4",
  "completion_status": "complete|complete_with_review",
  "contract_items": [
    {
      "contract_id": "<source contract ID>",
      "contract_text": "Полный текст пункта",
      "matrix_ids": ["<source matrix ID>"],
      "status": "aligned|deviation|extra_in_contract|not_applicable|needs_review",
      "comment": "Проверяемое обоснование с цитатами и дельтой"
    }
  ],
  "matrix_items": [
    {
      "matrix_id": "<source matrix ID>",
      "matrix_text": "Полный текст строки матрицы",
      "required_type": "mandatory",
      "status": "missing_in_contract",
      "comment": "Почему требование применимо и почему аналога нет во всём договоре"
    }
  ],
  "review_items": [
    {
      "review_id": "review-1",
      "review_type": "uncertain_mapping|uncertain_applicability",
      "contract_ids": ["<source contract ID>"],
      "matrix_ids": ["<source matrix ID>"],
      "issue": "Что именно нельзя разрешить по источникам",
      "question_for_lawyer": "Один конкретный вопрос, ответ на который завершит классификацию"
    }
  ]
}
```

## Инварианты

### Верхний уровень

- Использовать только `schema_version: contract-matrix-map.v4`.
- Использовать `complete`, если `review_items` пуст.
- Использовать `complete_with_review`, если `review_items` не пуст.
- Всегда включать массивы `contract_items`, `matrix_items` и `review_items`.

### `contract_items`

- Включить каждый собственный номер основного текста договора ровно один раз.
- Сохранить полный относящийся текст в `contract_text`.
- Не повторять `contract_id`.
- Не повторять matrix ID внутри одной строки.
- Для `aligned` и `deviation` указать хотя бы один matrix ID.
- Для `extra_in_contract` и `not_applicable` оставить `matrix_ids: []`.
- Для `needs_review` указать смысловых кандидатов в `matrix_ids` и создать
  связанный `review_item`.
- Не использовать `needs_review` для сомнения, которое разрешается чтением
  доступного текста.

### `matrix_items`

- Включать только подтверждённые `missing_in_contract`.
- Включать только applicable mandatory-строки.
- Не включать optional, structural, not-applicable и uncertain-строки.
- Не включать matrix ID, уже использованный в `contract_items`.
- Не повторять `matrix_id`.

### `review_items`

- Использовать стабильный уникальный `review_id`.
- `uncertain_mapping` должен ссылаться минимум на один contract ID и один
  matrix ID.
- `uncertain_applicability` должен ссылаться минимум на один matrix ID;
  `contract_ids` может быть пуст.
- Не помещать matrix ID одновременно в `review_items` и подтверждённые
  `matrix_items`.
- Формулировать `issue` как дефицит решения, а не общий пересказ.
- Формулировать один вопрос, который юрист может разрешить без повторного
  полного анализа.
