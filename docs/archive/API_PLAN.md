# API · общий контракт бэка и фронта

> Архив хакатонного проектирования. Документ содержит первоначальные планы; действующие инструкции — в [документации сервиса](../README.md).

Источник правды для обеих сторон. Бэк реализует, фронт пишет против него (сначала в мок-режиме). Контекст — в [README.md](PROJECT_PLAN.md), смысл полей риска, сигналов и стратегий — в [BACKEND.md](BACKEND_PLAN.md), разделы 4–5 и 13–16.

---

## 6. API

Базовый URL: `/api/v1`. Формат JSON, время в ISO 8601 с таймзоной. Документация OpenAPI генерируется автоматически: `/docs` и `/openapi.json`.

**Дополнение 0.5:** работают `GET/POST /objects/{id}/valuation` и `GET /objects/{id}/valuation/history`; `capabilities.valuation=true`. Это стоимость строительства наружного контура по демонстрационным тарифам, а не рыночная оценка или переоценка залога. Полный контракт, источники данных и ограничения — в [VALUATION.md](../VALUATION.md). В проект добавлена `total_area_m2`; карточка включает `valuation` и `valuation_inputs`.

**Подключение ML:** детектор и классификатор материала связаны через рамки стен. В `per_view` добавлены `cls`, `bbox`, `detector_confidence`, `material_conf`: `conf` новых наблюдений — уверенность детектора, `confidence` — материала; совместимый `class` остаётся материалом. В `meta` загрузки можно передать `target_bbox` для фильтрации. Автоматическое определение объекта по кадастру пока недоступно (`automatic_target_identification=false`). Точный формат и настройки — в [YOLO_INTEGRATION.md](../YOLO_INTEGRATION.md).

**Пороги материала:** `min_crop_confidence=0.8` и `min_object_confidence=0.8` читаются из `configs/materials.yaml`. Ответ вырезки ниже порога становится `unclear`; исходное имя хранится в `predicted_material`, вероятность — в `material_conf`. Версия и пороги выдаются в `material_result.rules/thresholds` и `/meta` (`material_rules_version`, `material_thresholds`).

**Дополнение 0.4:** работают `GET /llm/status`, `POST/GET /objects/{id}/analysis` и `GET /analyses/{analysis_id}`. Контракт и запуск — в [LLM.md](../LLM.md). Это AI-сводка инспекции с `capabilities.llm_analysis`; полный `/conductor`, финансовые стратегии и банковский риск ниже остаются планом.

**Общая сборка материалов и готовности, версия 0.3.** Запуск компонентов и изменения полей проверки — в [INTEGRATION.md](../INTEGRATION.md). Точный контракт текущего модуля и порядок подключения фронта — в [MATERIAL_API.md](../API.md), команды запуска — в [README сервиса](../../README.md). Сейчас реализованы `health`, `meta`, создание/список/карточка объектов, загрузка/список/получение облётов, снимки/оригиналы/превью/кропы, агент готовности, обе ручные проверки, история облётов и журнал проверок. Остальные эндпоинты этого документа остаются планом общего сервиса. Схемы реально работающих запросов доступны в `/docs`.

Особенности первой версии:
- `POST /objects` принимает `address`, необязательные `cadastral`, `notes`, идентификатор кредита, сроки, признаки ПЗ/регистрации/залога/наличия фото, `loan`, `checkpoints` и `project` (материал, этажность, площадь, источник). Эти поля валидируются и сохраняются; риск и готовность по ним пока не рассчитываются. `parcel_geojson` и `vri` принимаются; правовой анализ ВРИ пока не выполняется.
- `/meta` сообщает `scope: inspection`, `capabilities`, материалы, ракурсы и состояния материала. Отсутствующие функции риска, задач и дирижёра не должны блокировать подключение этого модуля.
- Список и карточка объекта возвращают `last_flight_id`, `last_flight_at`, `last_flight`, `material_status`, `data_status`. Закреплённый `object.material` содержит `project` и `match`. Незакреплённое предположение доступно в `last_flight.material_result`, даже если `object.material = null`.
- У загрузки облёта добавлено поле формы `target_confirmed: bool = false`: сотрудник подтверждает, что на снимках нужное здание. При отсутствии EXIF нужен ручной `view`; GPS можно передать через `meta`.
- Без ручного ракурса `view` может быть `null`. Наклон камеры позволяет исключить верхние кадры, но автоматически установить сторону фасада нельзя. `quality_ok: null` означает, что модель качества ещё не подключена; `technical_ok: true` подтверждает только техническую проверку файла.
- `GET /flights/{id}` сохраняет общий формат; `result` содержит готовность, `land_result` пока `null`. `material_result` дополнен `status`, `is_demo` и `review_reasons`. В `per_view` возвращаются `class/conf` общего контракта и совместимые `material/confidence`. Состояния описаны в [объяснении бэкенда](../HOW_IT_WORKS.md).
- `POST /flights/{id}/material/review`: `{"action":"accept"}` принимает результат модели; `{"action":"correct","material":"brick","comment":"Причина"}` сохраняет правку. Автор — `X-User`. Исправление материала находится в `material_review`, готовности — в `readiness_review` и `review`; ответ модели не перезаписывается, подтверждённый материал доступен в карточке объекта.
- `GET /images/{image_id}/crops/{crop_index}` выдаёт участок стены по рамке модели. Используйте полный `crop_url` из результата: он содержит параметр `revision`, чтобы повторная обработка не меняла ранее выданный кроп. До обработки превью отвечает `409 PREVIEW_NOT_READY`.
- Загрузка отклоняет дубликаты внутри пакета (`400 DUPLICATE_IMAGE`) и повреждённые изображения (`415 INVALID_IMAGE`). При ошибке одного файла весь пакет отклоняется. Ошибка полного декодирования или инференса в воркере сохраняется в облёте как `failed`.
- В деморежиме `MOCK=1` снимки действительно сохраняются и обрабатываются, но материала модель не определяет: возвращается `model_unavailable`, `is_demo: true`. Такой ответ нельзя принять как реальное распознавание.

### 6.1 Сущности

```
Object (объект залога: поля реестра банка)
 ├── 1 Loan (ПЗ, DPD, транши — необязательно)        ← реестр / АБС / синтетика
 ├── * Checkpoint (стадия к дате — необязательно)
 ├── 0..1 Project (материал, этажность, площадь — необязательно)
 ├── * Flight (облёт) 1 ── * Image (снимок) 1 ── * Detection
 │          ├── 1 Result (вывод агента готовности)
 │          ├── 0..1 MaterialResult, 0..1 LandResult
 │          ├── 1 Act (акт осмотра)
 │          └── * Review (правки человека)
 ├── * RiskAssessment (оценка риска; последняя — текущая)
 ├── * ConductorDecision (рекомендация AI-модель, проверка, путь по схеме, стратегия; последнее — текущее)
 ├── * Step (шаг дирижёра и его исход: звонок, проверка, SMS, облёт)
 ├── * Valuation (оценка стоимости)
 └── * Alert (тревога) 1 ── * Action (решение по тревоге)
```

Есть три разных показателя, их нельзя путать:
- `risk_level` (`green / yellow / orange / red`) — **риск клиента**, насколько срочно;
- `strategy` (`S1`…`S8` и состояние) — **что банк делает с клиентом** по схеме дирижёра;
- `data_status` (`ok / review / reshoot / processing / no_data`) — **состояние данных**: можно ли верить последнему облёту.

### 6.2 Эндпоинты

**Объекты**

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/objects` | создать объект |
| `POST` | `/objects/import` | импорт реестра банка (xlsx/csv) |
| `GET` | `/objects` | список; фильтры `risk_level`, `strategy`, `stage`, `data_status`, `overdue`, `pledged`, `q` (поиск по адресу, кадастру, кредиту), `sort` (по умолчанию `-risk`), `page`, `page_size` |
| `GET` | `/objects/{object_id}` | карточка: объект + кредит + срок + риск + дирижёр + последний облёт + освоение + оценка |
| `GET` | `/objects/{object_id}/history` | план, факт и прогноз для графика прогресса |
| `PUT` | `/objects/{object_id}/loan` | обновить ПЗ, DPD, транши; на хакатоне — ручной ввод |

**Риск и тревоги**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/objects/{object_id}/risk` | текущая оценка риска (раздел 4.7) |
| `GET` | `/objects/{object_id}/risk/history` | как менялся риск по датам |
| `POST` | `/risk/recalculate` | пересчитать риск и дирижёра по всем объектам (то же делает ежедневный cron) |
| `GET` | `/alerts` | лента тревог; фильтры `risk_level`, `state` (`open / in_progress / closed`), `page` |
| `POST` | `/alerts/{alert_id}/actions` | подтвердить, отклонить или закрыть тревогу, записать результат |
| `GET` | `/objects/{object_id}/actions` | журнал шагов и решений по объекту |

**Дирижёр**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/objects/{object_id}/conductor` | проверенная рекомендация AI-модель или резерв по правилам, путь по схеме, стратегия, следующий шаг (раздел 13.7) |
| `GET` | `/objects/{object_id}/conductor/history` | решения по датам |
| `POST` | `/objects/{object_id}/steps` | исход шага, которого ждёт дирижёр (звонок, проверка, размещение, торги); дирижёр сразу идёт дальше |
| `POST` | `/objects/{object_id}/strategy` | подтвердить или отклонить предложенную стратегию |
| `GET` | `/tasks` | очередь шагов, которые ждут человека; фильтры `step`, `strategy`, `risk_level`, `page` |

**Облёты**

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/objects/{object_id}/flights` | загрузить снимки (multipart), ответ `202` со статусом `queued` |
| `GET` | `/objects/{object_id}/flights` | все облёты объекта |
| `GET` | `/flights/{flight_id}` | статус обработки и результат |
| `GET` | `/flights/{flight_id}/images` | снимки с детекциями |
| `GET` | `/flights/{flight_id}/act` | акт осмотра; `?format=html` — для печати (раздел 16) |
| `GET` | `/images/{image_id}/file` | оригинал |
| `GET` | `/images/{image_id}/thumb` | превью, по длинной стороне 512 px |

**Оценка**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/objects/{object_id}/valuation` | текущий расчёт стоимости постройки, контракт 0.5 в [VALUATION.md](../VALUATION.md) |
| `POST` | `/objects/{object_id}/valuation` | сохранить параметры: `area_m2`, `unit_cost_rub_m2`, `rate_note`, `land_rub` |
| `GET` | `/objects/{object_id}/valuation/history` | сохранённые снимки расчёта с признаком `stale` |

**Человек в контуре**

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/flights/{flight_id}/review` | принять или исправить результат |
| `POST` | `/flights/{flight_id}/reshoot` | вручную отправить на пересъёмку |

**Служебные**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/meta` | версии модели и правил, классы, стадии, шкала банка, уровни риска, сигналы, стратегии, шаги, цвета |
| `GET` | `/config/stages`, `/config/risk`, `/config/conductor` | веса этапов, пороги отставания и уровней риска, пороги срока дирижёра (P1) |
| `PUT` | `/config/stages`, `/config/risk`, `/config/conductor` | изменить их без релиза (P1) |
| `GET` | `/health` | состояние сервиса |

### 6.3 Контракты

#### `POST /objects`

Запрос с минимальным набором — поля реестра банка:
```json
{
  "cadastral": "54:19:000001:17",
  "address": "Новосибирская обл, Новосибирский р-н, с Демо, ул Лесная, уч 12",
  "loan_id": "IZH-2025-0412",
  "started_at": "2025-10-27",
  "planned_completion_at": "2027-10-31",
  "actual_completion_at": null,
  "overdue": false,
  "registered": false,
  "pledged": false,
  "has_aerial_photo": true
}
```

Обязательные поля: `cadastral`, `address`, `started_at`, `planned_completion_at`, `overdue`, `registered`, `pledged`. Необязательные:

```json
{
  "parcel_geojson": {"type": "Polygon", "coordinates": [[[82.870, 55.011], [82.872, 55.011], [82.872, 55.013], [82.870, 55.013], [82.870, 55.011]]]},
  "vri": "2.1",
  "loan": {
    "amount": 6000000,
    "dpd": 0,
    "collateral_value_rub": 5200000,
    "tranches": [
      {"n": 1, "amount": 2000000, "paid_at": "2025-11-05"},
      {"n": 2, "amount": 2500000, "condition_stage": "walls", "paid_at": null}
    ]
  },
  "checkpoints": [
    {"stage": "foundation", "due": "2026-06-30"},
    {"stage": "walls",      "due": "2026-09-30"}
  ],
  "project": {
    "floors": 2,
    "footprint_m2": 96,
    "wall_material": "aerated_concrete",
    "source": "Проект ИЖС №17-2026, лист 3"
  }
}
```

- Без `checkpoints` план линейный от `started_at` до `planned_completion_at` (раздел 4.2).
- Без `project.wall_material` нет сравнения материала с проектом (раздел 5.5); этажность и площадь из проекта нужны оценке (раздел 15).
- Без `parcel_geojson` границы берутся по кадастровому номеру (на хакатоне — из `mocks/parcels/`). Если их нет и там, проверка участка идёт по GPS (раздел 4.4), а освоение получает `status: no_parcel`.

Ответ `201`: объект целиком (как в карточке).

#### `POST /objects/import`

`multipart/form-data`, поле `file`: xlsx или csv в формате реестра банка. Первая строка — заголовки:

| Колонка реестра | Поле | Преобразование |
|---|---|---|
| Территория | `address` | — |
| Кадастровый номер | `cadastral` | ключ: повторный импорт обновляет объект, а не создаёт новый |
| Плановая дата | `planned_completion_at` | `дд.мм.гггг` |
| Фактическая дата | `actual_completion_at` | `дд.мм.гггг`, «Нет» → `null` |
| Наличие ПЗ | `overdue` | Да / Нет |
| ОН зарегистрирован | `registered` | Да / Нет |
| Залог | `pledged` | Да / Нет |
| Дата начала строительства | `started_at` | `дд.мм.гггг` |
| Наличие фото с неба | `has_aerial_photo` | Да / Нет |

Ответ `200`:
```json
{
  "created": 30,
  "updated": 4,
  "errors": [
    {"row": 7, "code": "VALIDATION_ERROR", "message": "Плановая дата раньше даты начала строительства"}
  ]
}
```
Строки с ошибками пропускаются, остальные импортируются. После импорта пересчитываются риск и дирижёр по затронутым объектам.

#### `GET /objects`

```json
{
  "items": [
    {
      "id": "obj_demo_2",
      "cadastral": "54:19:000001:17",
      "loan_id": "IZH-2025-0412",
      "address": "Новосибирская обл, Новосибирский р-н, с Демо, ул Лесная, уч 12",
      "started_at": "2025-10-27",
      "planned_completion_at": "2027-10-31",
      "term_left_pct": 50,
      "overdue": false,
      "pledged": false,
      "last_flight_id": "fl_88",
      "last_flight_at": "2026-10-28T11:40:00+07:00",
      "stage": "foundation",
      "stage_label": "Фундамент",
      "readiness_pct": 15,
      "bank_readiness": 50,
      "plan_pct": 50,
      "lag_days": 256,
      "forecast_completion_at": null,
      "risk_level": "orange",
      "top_signal": {"code": "LAG", "label": "Отставание от плана"},
      "strategy": null,
      "next_step": {"code": "COMM_REASON", "label": "Узнать причину незавершения"},
      "data_status": "ok",
      "open_alert_id": "al_9"
    }
  ],
  "total": 34,
  "page": 1,
  "page_size": 20
}
```

Сортировка по умолчанию: `red` → `orange` → `yellow` → `green`, внутри уровня — по `lag_days` по убыванию.

`strategy` — `{"code", "label", "state"}` или `null`, если дирижёр ещё не дошёл до стратегии. `term_left_pct` меньше нуля — срок истёк.

`data_status` — состояние данных последнего облёта:

| `data_status` | Когда |
|---|---|
| `ok` | результат есть, уверенность высокая или принят человеком |
| `review` | нужна проверка: средняя уверенность, ещё нет решения человека |
| `reshoot` | нужна пересъёмка |
| `processing` | облёт обрабатывается |
| `no_data` | облётов ещё не было |

#### `GET /objects/{object_id}`

```json
{
  "id": "obj_demo_2",
  "cadastral": "54:19:000001:17",
  "loan_id": "IZH-2025-0412",
  "address": "Новосибирская обл, Новосибирский р-н, с Демо, ул Лесная, уч 12",
  "started_at": "2025-10-27",
  "planned_completion_at": "2027-10-31",
  "actual_completion_at": null,
  "term_left_pct": 50,
  "registered": false,
  "pledged": false,
  "has_aerial_photo": true,
  "parcel_geojson": { "...": "..." },
  "vri": "2.1",
  "loan": { "...": "как в POST /objects или null" },
  "checkpoints": [],
  "project": null,
  "risk": { "...": "оценка риска, раздел 4.7" },
  "conductor": { "...": "решение дирижёра, раздел 13.7" },
  "data_status": "ok",
  "last_flight": { "...": "объект как в GET /flights/{id}" },
  "material": {
    "project": null,
    "observed": "aerated_concrete",
    "confidence": 0.84,
    "match": "no_project",
    "fixed_at_flight_id": "fl_88",
    "reviewed": false
  },
  "land": { "...": "выход раздела 14.5 или null" },
  "valuation": { "...": "выход раздела 15.5 или null" },
  "next_flight_planned": "2026-11-11"
}
```

`checkpoints[].state` (если график есть): `upcoming` (срок не наступил), `met` (стадия подтверждена облётом), `missed` (срок прошёл, стадии нет), `unverified` (срок прошёл, облёта после него нет).

#### `GET /objects/{object_id}/history`

```json
{
  "plan": [
    {"date": "2025-10-27", "pct": 0},
    {"date": "2027-10-31", "pct": 100}
  ],
  "fact": [
    {"flight_id": "fl_88", "flown_at": "2026-10-28T11:40:00+07:00", "stage": "foundation", "readiness_pct": 15, "bank_readiness": 50, "reviewed": false, "risk_level": "orange"}
  ],
  "forecast": {"completion_at": null, "on_time": false, "basis": "from_start"},
  "steps": [
    {"date": "2026-10-29", "code": "COMM_REASON", "label": "Узнать причину незавершения", "outcome": "underfunded", "comment": "Подрядчик поднял цену, денег на стены не хватает"}
  ]
}
```
Фронт рисует на одном графике плановую линию, точки факта, прогноз пунктиром и отметки шагов. Если человек исправил результат, в `fact` показываются **исправленные** значения, а `reviewed: true`. При графике стадий в `plan` — точки контрольных сроков.

#### `GET /alerts`

```json
{
  "items": [
    {
      "id": "al_9",
      "object_id": "obj_demo_2",
      "address": "Новосибирская обл, Новосибирский р-н, с Демо, ул Лесная, уч 12",
      "created_at": "2026-10-28T12:00:00+07:00",
      "risk_level": "orange",
      "signals": [{"code": "LAG", "label": "Отставание от плана", "text": "К 28.10.2026 по плану 50%, облёт: фундамент, 15%. Отставание 256 дней"}],
      "recommended_action": {"code": "COMM_REASON", "label": "Связаться с клиентом, узнать причину незавершения", "requires_confirmation": true},
      "overdue": false,
      "state": "open",
      "assignee": null
    }
  ],
  "total": 7
}
```

Тревога создаётся, когда уровень риска объекта **растёт**, а не при каждом пересчёте. Пока тревога открыта, повторный такой же сигнал её не дублирует.

#### `POST /alerts/{alert_id}/actions`

```json
{
  "decision": "confirm",
  "action_code": "COMM_REASON",
  "outcome": "Звонок назначен на завтра",
  "close_alert": false
}
```
`decision`: `confirm` (мера принята, дирижёр ставит шаг в очередь), `reject` (тревога ложная, причина в `outcome`), `done` (мера выполнена, результат в `outcome`). Отклонённые тревоги — материал для подстройки порогов. Коды мер — из `/meta.steps`.

#### `POST /objects/{object_id}/steps`

Исход шага, которого ждёт дирижёр:
```json
{
  "step": "COMM_REASON",
  "outcome": "underfunded",
  "comment": "Подрядчик поднял цену, денег на стены не хватает"
}
```

| `step` | `outcome` |
|---|---|
| `COMM_REASON` | `underfunded`, `hardship`, `other`, `failed` |
| `CHECK_UNDERFUNDED`, `CHECK_HARDSHIP` | `yes`, `no` |
| `COMM_SETTLEMENT`, `COMM_COLLATERAL`, `COMM_PLEDGE` | `agreed`, `declined` |
| `MANUAL_INSPECTION` | `confirmed`, `rejected` |
| `LISTING` | `done` |
| `AUCTION` | `sold`, `not_sold` |

Шаг `CONFIRM_STRATEGY` сюда не отправляется: стратегия подтверждается через `POST /objects/{object_id}/strategy`.

Ответ `201`: новое решение дирижёра (как `GET /objects/{id}/conductor`), при ожидании AI-модель — с `llm_status: pending`. Вызов LLM не блокирует HTTP-запрос. Если дирижёр сейчас не ждёт этот шаг — `409 STEP_NOT_EXPECTED`.

#### `POST /objects/{object_id}/strategy`

```json
{"decision": "confirm", "strategy": "S3", "comment": "Согласовано с клиентом, готовим доп. финансирование"}
```
`decision`: `confirm` или `reject` (с причиной в `comment`). Ответ `201`: новое решение дирижёра. Если такой стратегии нет в состоянии `proposed` — `409 STRATEGY_NOT_PROPOSED`.

#### `GET /objects/{object_id}/conductor`

Выход раздела 13.7: `strategy`, `path` (узел, ответ, доказательство), `next_step`, `explanation` и поля рекомендации:

| Поле | Контракт |
|---|---|
| `decision_source` | `llm` — применена проверенная рекомендация; `rules_fallback` — используется дерево банка |
| `llm_status` | `pending` — запрос в очереди или обрабатывается; `accepted` — ответ проверен; `unavailable` — API недоступен или не настроен; `rejected` — ответ не прошёл проверку или устарел |
| `recommendation` | объект ниже при `accepted`; в остальных случаях `null` |

Формат `recommendation` (все поля обязательны):

```json
{
  "strategy_code": "S4",
  "next_step_code": "CONFIRM_STRATEGY",
  "reason": "Дом не достроен к сроку, клиент согласился заменить обеспечение. Предложить замену обеспечения сотруднику на подтверждение.",
  "evidence_refs": ["readiness.bank_readiness", "term.expired", "steps.st_44.outcome"],
  "missing_data": []
}
```

- `strategy_code` — один код `S1`…`S8` из допустимого для текущего объекта списка или `null`, если сначала нужно получить данные или выполнить шаг.
- `next_step_code` — допустимый код из `/meta.steps` либо `null`, если ожидается внешнее событие. `reason` — краткое объяснение по фактам; `missing_data` — массив имён недостающих полей.
- `evidence_refs` — массив ключей фактов в снимке контекста запроса. Ключи назначает бэкенд: например, `readiness.bank_readiness`, `term.expired`, `steps.st_44.outcome`. Каждая ссылка должна существовать, а её значение — подтверждать рекомендацию. Адреса и персональные данные в контекст не включаются.
- `decision_source: llm` допустим только вместе с `llm_status: accepted` и непустым объектом `recommendation`. При `rules_fallback` используются `pending`, `unavailable` или `rejected`, а `recommendation = null`. Исходный отклонённый ответ сохраняется только для аудита.
- `path`, `strategy`, `next_step` и требования подтверждения формирует бэкенд после проверки. `accepted` не означает подтверждения стратегии сотрудником: для стратегий 2, 3, 4, 6, 7, 8 остаётся `strategy.state: proposed` до `POST /objects/{id}/strategy`.
- Пока `llm_status = pending`, фронт опрашивает этот эндпоинт раз в 2 секунды. Резервное решение по правилам доступно сразу; тайм-аут завершает ожидание статусом `unavailable`. Сигналы отставания рассчитываются независимо от LLM.

При пересчёте API не заменяет подтверждённую стратегию новым предложением автоматически. Вызовы `POST /objects/{id}/strategy` подтверждают только текущее предложение, уже прошедшее проверку бэкенда. Поведение воркера и резерв описаны в разделе 13.8 [BACKEND.md](BACKEND_PLAN.md).

#### `GET /tasks`

```json
{
  "items": [
    {
      "id": "st_41",
      "object_id": "obj_demo_2",
      "address": "Новосибирская обл, Новосибирский р-н, с Демо, ул Лесная, уч 12",
      "step": {"code": "COMM_REASON", "label": "Узнать причину незавершения"},
      "strategy": null,
      "risk_level": "orange",
      "created_at": "2026-10-28T12:00:00+07:00"
    }
  ],
  "total": 5
}
```

#### `POST /objects/{object_id}/flights`

`multipart/form-data`:

| Поле | Тип | Обязательное | Описание |
|---|---|---|---|
| `files` | файл[] | да | JPG/PNG, до 30 МБ каждый, 1–20 файлов |
| `views` | строка | нет | JSON-массив той же длины, что `files`: `["N","E","S","W","top_low","top_high"]`; `null` — определить из EXIF. Без EXIF обязателен |
| `meta` | строка | нет | JSON-массив той же длины, что `files`: `[{"gps": [55.0120, 82.8710]}, …]`; нужен, если в файлах нет EXIF |
| `flown_at` | строка | нет | дата облёта; по умолчанию берётся из EXIF |

Ответ `202`:
```json
{"flight_id": "fl_88", "status": "queued"}
```

#### `GET /flights/{flight_id}`

```json
{
  "flight_id": "fl_88",
  "object_id": "obj_demo_2",
  "status": "done",
  "flown_at": "2026-10-28T11:40:00+07:00",
  "uploaded_at": "2026-10-28T15:02:11+07:00",
  "images_count": 6,
  "result": { "...": "выход агента готовности, см. 3.6" },
  "material_result": { "...": "выход агента материала, см. 5.6; null, если стен не видно" },
  "land_result": { "...": "выход освоения участка, см. 14.5" },
  "review": null,
  "error": null
}
```

`status`: `queued` → `processing` → `done` | `failed`. При `failed` поле `error` содержит объект ошибки (см. 6.4), а `result` равен `null`.

**Фронт опрашивает этот эндпоинт раз в 2 секунды**, пока статус не станет `done` или `failed`.

#### `GET /flights/{flight_id}/images`

```json
[
  {
    "id": "img_301",
    "view": "N",
    "view_source": "exif",
    "width": 5472,
    "height": 3648,
    "url": "/api/v1/images/img_301/file",
    "thumb_url": "/api/v1/images/img_301/thumb",
    "sha256": "9f2c…",
    "gps": [55.0120, 82.8710],
    "gps_source": "exif",
    "taken_at": "2026-10-28T11:41:12+07:00",
    "quality_ok": true,
    "detections": [
      {
        "class": "foundation",
        "label": "Фундамент",
        "conf": 0.93,
        "bbox": [0.075, 0.063, 0.345, 0.332]
      }
    ]
  }
]
```

**`bbox` = `[x1, y1, x2, y2]`, нормализованные от 0 до 1** относительно ширины и высоты снимка. Одинаково подходят и для превью, и для оригинала. `view_source`: `exif` или `manual`; `gps_source`: `exif` или `meta`.

#### `GET /flights/{flight_id}/act`

```json
{
  "flight_id": "fl_88",
  "loan_id": "IZH-2025-0412",
  "inspected_at": "2026-10-28T11:40:00+07:00",
  "fields": [
    {"id": "address",        "label": "Адрес объекта",          "value": "Новосибирская обл, …", "source": "registry"},
    {"id": "wall_material",  "label": "Материал наружных стен", "value": "Газобетон / пеноблок",  "source": "drone"},
    {"id": "rooms",          "label": "Количество комнат",      "value": null,                     "source": "employee"}
  ],
  "defects": ["Стены не возведены", "Кровля отсутствует"],
  "condition_suggested": "unsatisfactory",
  "condition": null,
  "inspector": null,
  "images": ["img_301", "img_302", "img_303", "img_304", "img_305", "img_306"]
}
```
`source`: `registry`, `project`, `drone`, `employee`. Поля с `employee` и `value: null` заполняет сотрудник. `?format=html` отдаёт ту же форму для печати.

#### `POST /flights/{flight_id}/review`

Принять:
```json
{"action": "accept", "comment": null}
```
Исправить:
```json
{
  "action": "correct",
  "stage": "walls",
  "readiness_pct": 40,
  "comment": "Стропила не установлены, на снимке N штабель досок"
}
```

Ответ `201`:
```json
{
  "id": "rev_12",
  "flight_id": "fl_88",
  "action": "correct",
  "stage": "walls",
  "readiness_pct": 40,
  "comment": "…",
  "author": "ivanov",
  "created_at": "2026-10-28T16:10:00+07:00"
}
```

Результат модели **не перезаписывается**: правка хранится отдельно. Это и журнал аудита, и разметка для дообучения. В интерфейсе показывается последняя правка, если она есть. После правки пересчитываются риск, прогноз и дирижёр.

#### `GET /meta`

```json
{
  "model_version": "yolo26m-det@v1",
  "material_model_version": "yolo26m-cls@v1",
  "rules_version": "stages@v1",
  "conductor_version": "conductor@v1",
  "conductor_prompt_version": "conductor-prompt@v1",
  "mock": false,
  "views": [
    {"id": "top_low", "label": "Сверху: дом"},
    {"id": "top_high", "label": "Сверху: участок"},
    {"id": "N", "label": "Север"},
    {"id": "E", "label": "Восток"},
    {"id": "S", "label": "Юг"},
    {"id": "W", "label": "Запад"}
  ],
  "stages": [
    {"id": "none",       "label": "Нет работ",    "order": 0},
    {"id": "foundation", "label": "Фундамент",    "order": 1},
    {"id": "walls",      "label": "Стены",        "order": 2},
    {"id": "roof",       "label": "Кровля",       "order": 3},
    {"id": "openings",   "label": "Окна и двери", "order": 4},
    {"id": "facade",     "label": "Фасад",        "order": 5},
    {"id": "done",       "label": "Готов",        "order": 6}
  ],
  "bank_readiness": [
    {"id": 0,   "label": "Ничего нет"},
    {"id": 50,  "label": "Фундамент, стены, возможно каркас крыши"},
    {"id": 100, "label": "Стены, крыша, окна и двери"}
  ],
  "classes": [
    {"id": "walls_full", "label": "Стены выведены", "color": "#2E7D32"}
  ],
  "materials": [
    {"id": "brick",            "label": "Кирпич",               "group": "stone"},
    {"id": "aerated_concrete", "label": "Газобетон / пеноблок", "group": "blocks"},
    {"id": "ceramic_block",    "label": "Керамический блок",    "group": "blocks"},
    {"id": "timber",           "label": "Брус / бревно",        "group": "wood"},
    {"id": "frame",            "label": "Каркас",               "group": "wood"},
    {"id": "sip",              "label": "СИП-панели",           "group": "wood"},
    {"id": "monolith",         "label": "Монолит",              "group": "concrete"},
    {"id": "hidden",           "label": "Скрыт отделкой",       "group": null},
    {"id": "unclear",          "label": "Не определить",        "group": null}
  ],
  "risk_levels": [
    {"id": "green",  "label": "По плану",           "color": "#2E7D32", "order": 0},
    {"id": "yellow", "label": "Наблюдать",          "color": "#F9A825", "order": 1},
    {"id": "orange", "label": "Связаться с клиентом", "color": "#EF6C00", "order": 2},
    {"id": "red",    "label": "Срочно",             "color": "#C62828", "order": 3}
  ],
  "signals": [
    {"id": "LAG",             "label": "Отставание от плана",     "severity": "high"},
    {"id": "DEADLINE_PASSED", "label": "Срок строительства истёк", "severity": "high"},
    {"id": "SETBACK_SUSPECT", "label": "Отступ от границы меньше нормы", "severity": "info", "affects_risk": false}
  ],
  "strategies": [
    {"id": "S1", "label": "Вылет дрона",                            "requires_confirmation": false},
    {"id": "S2", "label": "Реструктуризация",                       "requires_confirmation": true},
    {"id": "S3", "label": "Доп. финансирование на достройку ОН",    "requires_confirmation": true},
    {"id": "S4", "label": "Замена обеспечения",                     "requires_confirmation": true},
    {"id": "S5", "label": "Мониторинг",                             "requires_confirmation": false},
    {"id": "S6", "label": "Передача в суд",                         "requires_confirmation": true},
    {"id": "S7", "label": "Регистрация ОН и оформление в залог",    "requires_confirmation": true},
    {"id": "S8", "label": "Реализация ОН",                          "requires_confirmation": true}
  ],
  "steps": [
    {"id": "SMS_TERM_REMINDER", "label": "SMS/PUSH: напоминание о плановой дате", "kind": "auto"},
    {"id": "FLIGHT_TASK",       "label": "Полётное задание и SMS об осмотре",     "kind": "auto"},
    {"id": "COMM_REASON",       "label": "Узнать причину незавершения",           "kind": "employee",
     "outcomes": [{"id": "underfunded", "label": "Неполное финансирование"}, {"id": "hardship", "label": "Трудная жизненная ситуация"}, {"id": "other", "label": "Другое"}, {"id": "failed", "label": "Не удалось связаться"}]},
    {"id": "CHECK_UNDERFUNDED", "label": "Неполное финансирование по договору?",  "kind": "employee",
     "outcomes": [{"id": "yes", "label": "Да"}, {"id": "no", "label": "Нет"}]},
    {"id": "CHECK_HARDSHIP",    "label": "Трудная жизненная ситуация?",           "kind": "employee",
     "outcomes": [{"id": "yes", "label": "Да"}, {"id": "no", "label": "Нет"}]},
    {"id": "COMM_SETTLEMENT",   "label": "Предложить инструменты урегулирования", "kind": "employee",
     "outcomes": [{"id": "agreed", "label": "Согласен"}, {"id": "declined", "label": "Не согласен"}]},
    {"id": "COMM_COLLATERAL",   "label": "Предложить замену обеспечения",         "kind": "employee",
     "outcomes": [{"id": "agreed", "label": "Согласен"}, {"id": "declined", "label": "Не согласен"}]},
    {"id": "COMM_PLEDGE",       "label": "Оформить ОН в залог",                   "kind": "employee",
     "outcomes": [{"id": "agreed", "label": "Согласен"}, {"id": "declined", "label": "Не согласен"}]},
    {"id": "MANUAL_INSPECTION", "label": "Ручная проверка, выезд",                "kind": "employee",
     "outcomes": [{"id": "confirmed", "label": "Подтверждено"}, {"id": "rejected", "label": "Не подтвердилось"}]},
    {"id": "VALUATION",         "label": "AI-оценка ОН",                          "kind": "auto"},
    {"id": "LISTING",           "label": "Размещение на PortalDA, Авито",         "kind": "employee",
     "outcomes": [{"id": "done", "label": "Размещено"}]},
    {"id": "AUCTION",           "label": "Торги",                                 "kind": "employee",
     "outcomes": [{"id": "sold", "label": "Продано"}, {"id": "not_sold", "label": "Не продано"}]},
    {"id": "CONFIRM_STRATEGY",  "label": "Подтвердить стратегию",                 "kind": "employee"}
  ]
}
```

Фронт **не хардкодит** названия классов, материалов, стадий, шкалы банка, уровней риска, сигналов, стратегий, шагов, исходов и цвета: всё берётся из `/meta`.

### 6.4 Ошибки

Любая ошибка возвращается в одном формате:
```json
{"error": {"code": "NOT_ENOUGH_VIEWS", "message": "Нужны оба снимка сверху и минимум 3 фасада", "details": {}}}
```

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `VALIDATION_ERROR` | неверные поля запроса |
| 400 | `VIEWS_MISMATCH` | длина `views` или `meta` не совпадает с числом файлов |
| 400 | `VIEWS_REQUIRED` | в файлах нет EXIF, а `views` не передан |
| 400 | `IMPORT_BAD_COLUMNS` | в реестре нет обязательных колонок |
| 413 | `FILE_TOO_LARGE` | файл больше 30 МБ |
| 415 | `UNSUPPORTED_FORMAT` | не JPG/PNG (для облёта) или не xlsx/csv (для импорта) |
| 404 | `NOT_FOUND` | нет объекта, облёта или снимка |
| 409 | `FLIGHT_NOT_READY` | ревью или акт до окончания обработки |
| 409 | `STEP_NOT_EXPECTED` | дирижёр сейчас не ждёт этот шаг |
| 409 | `STRATEGY_NOT_PROPOSED` | стратегия не предложена или уже решена |
| 422 | `NOT_ENOUGH_VIEWS` | обработка невозможна (в `error` облёта) |
| 500 | `INFERENCE_FAILED` | ошибка модели (в `error` облёта) |

`code` стабильный, `message` на русском: фронт может показывать его пользователю как есть.
