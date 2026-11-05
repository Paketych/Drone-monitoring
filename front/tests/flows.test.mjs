import test from "node:test";
import assert from "node:assert/strict";
import { mockRequest, resetDemo, meta } from "../js/mock.js";
import { parseCSV, registryRows } from "../js/import.js";

test("От звонка до S3: исход AI не подтверждает стратегию сотрудником", async () => {
  resetDemo();
  let c = await mockRequest("/objects/obj_demo_2/steps", {
    method: "POST",
    body: {
      step: "COMM_REASON",
      outcome: "underfunded",
      comment: "Не хватает средств",
    },
  });
  assert.equal(c.next_step.code, "CHECK_UNDERFUNDED");
  assert.equal(c.strategy, null);
  c = await mockRequest("/objects/obj_demo_2/steps", {
    method: "POST",
    body: { step: "CHECK_UNDERFUNDED", outcome: "yes" },
  });
  assert.equal(c.strategy.code, "S3");
  assert.equal(c.strategy.state, "proposed");
  assert.equal(c.llm_status, "accepted");
  await assert.rejects(
    () =>
      mockRequest("/objects/obj_demo_2/steps", {
        method: "POST",
        body: { step: "COMM_REASON", outcome: "failed" },
      }),
    { code: "STEP_NOT_EXPECTED" },
  );
  c = await mockRequest("/objects/obj_demo_2/strategy", {
    method: "POST",
    body: { decision: "confirm", strategy: "S3" },
  });
  assert.equal(c.strategy.state, "confirmed");
  assert.equal(c.next_step, null);
  await assert.rejects(
    () =>
      mockRequest("/objects/obj_demo_2/strategy", {
        method: "POST",
        body: { decision: "confirm", strategy: "S3" },
      }),
    { code: "STRATEGY_NOT_PROPOSED" },
  );
});
test("Ложная тревога требует причины, повторный импорт обновляет кадастр", async () => {
  resetDemo();
  await assert.rejects(
    () =>
      mockRequest("/alerts/al_obj_demo_2/actions", {
        method: "POST",
        body: { decision: "reject", outcome: "" },
      }),
    { code: "VALIDATION_ERROR" },
  );
  await mockRequest("/alerts/al_obj_demo_2/actions", {
    method: "POST",
    body: { decision: "reject", outcome: "Ошибка снимка" },
  });
  const alerts = await mockRequest("/alerts");
  assert.ok(!alerts.items.some((a) => a.object_id === "obj_demo_2"));
});
test("Только допустимые исходы и сортировка по риску", async () => {
  resetDemo();
  await assert.rejects(
    () =>
      mockRequest("/objects/obj_demo_2/steps", {
        method: "POST",
        body: { step: "COMM_REASON", outcome: "invented" },
      }),
    { code: "VALIDATION_ERROR" },
  );
  const result = await mockRequest("/objects?page_size=20");
  assert.equal(result.total, 10);
  assert.equal(result.items[0].risk_level, "red");
  for (const o of result.items) {
    assert.ok(meta.risk_levels.some((r) => r.id === o.risk_level));
    assert.ok(meta.stages.some((s) => s.id === o.stage));
  }
});
test("CSV: кавычки, переносы, разделитель ;, BOM и даты", () => {
  const text =
    '\uFEFFТерритория;Кадастровый номер;Плановая дата;Фактическая дата;Наличие ПЗ;ОН зарегистрирован;Залог;Дата начала строительства;Наличие фото с неба\r\n"с. Тест, ул. Лесная; 1";54:19:1:1;31.10.2027;Нет;Нет;Да;Нет;27.10.2025;Да';
  const rows = registryRows(parseCSV(text));
  assert.equal(rows[0].address, "с. Тест, ул. Лесная; 1");
  assert.equal(rows[0].planned_completion_at, "2027-10-31");
  assert.equal(rows[0].overdue, false);
  assert.equal(rows[0].registered, true);
  assert.equal(rows[0].actual_completion_at, null);
  assert.throws(
    () => registryRows([["Неверная колонка"], ["1"]]),
    /Не найдены колонки/,
  );
  assert.throws(() => parseCSV('a,b\n"x,y'), /не закрыта кавычка/);
});
test("Исправление не уничтожает исходный вывод модели", async () => {
  resetDemo();
  await mockRequest("/flights/fl_2/review", {
    method: "POST",
    user: "Проверяющий",
    body: {
      action: "correct",
      stage: "walls",
      readiness_pct: 40,
      comment: "Стены видны",
    },
  });
  const o = await mockRequest("/objects/obj_demo_2");
  assert.equal(o.last_flight.result.readiness_pct, 15);
  assert.equal(o.last_flight.review.readiness_pct, 40);
  const h = await mockRequest("/objects/obj_demo_2/history");
  assert.equal(h.fact[0].readiness_pct, 40);
  assert.equal(h.fact[0].reviewed, true);
});
