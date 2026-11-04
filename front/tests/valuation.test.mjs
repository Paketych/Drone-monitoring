import assert from "node:assert/strict";
import test from "node:test";
import { constructionCostPanel } from "../js/valuation.js";
import { calculateDemoValuation } from "../js/valuation-demo.js";
import { mockRequest, resetDemo } from "../js/mock.js";

const helpers = {
  esc: (v) =>
    String(v)
      .replaceAll("&", "&amp;")
      .replaceAll('"', "&quot;")
      .replaceAll("<", "&lt;"),
  money: (v) => `${v} ₽`,
  label: (_, v) => v,
};

test("area and material calculate the full cost without inventing readiness", () => {
  const result = calculateDemoValuation(
    {},
    { area_m2: 100, wall_material: "brick" },
  );
  assert.equal(result.full_cost_rub, 6000000);
  assert.equal(result.completed_cost_rub, null);
  assert.equal(result.remaining_cost_rub, null);
  const html = constructionCostPanel({ id: "new", valuation: result }, helpers);
  assert.match(html, /Материал для расчёта/);
  assert.match(html, /value="brick"/);
  assert.match(html, /6000000 ₽/);
  assert.match(html, /Облёта ещё нет/);
  assert.match(html, /Загрузить фотографии/);
});

test("a completed inspection can be reviewed directly from the cost panel", () => {
  const result = calculateDemoValuation(
    {},
    { area_m2: 100, wall_material: "brick" },
  );
  result.breakdown.readiness_status = "target_not_confirmed";
  const html = constructionCostPanel(
    { valuation: result, last_flight: { status: "done" } },
    helpers,
  );
  assert.match(html, /data-action="review-correct"/);
  assert.match(html, /Не подтверждено, что на снимках нужный дом/);
  assert.doesNotMatch(html, /Загрузить фотографии/);
});

test("all configured materials use their own rates and progress uses the internal percentage", () => {
  const obj = {
    last_flight: { status: "done" },
    readiness_pct: 40,
    bank_readiness: 100,
  };
  for (const [material, full] of Object.entries({
    brick: 6000000,
    aerated_concrete: 5000000,
    ceramic_block: 5500000,
    timber: 4800000,
    frame: 4000000,
    sip: 4200000,
    monolith: 6500000,
  })) {
    const result = calculateDemoValuation(obj, {
      area_m2: 100,
      wall_material: material,
    });
    assert.equal(result.full_cost_rub, full);
    assert.equal(result.completed_cost_rub, full * 0.4);
    assert.equal(result.remaining_cost_rub, full * 0.6);
  }
});

test("demo inputs persist, material changes do not overwrite inspection, null restores automatic selection", async () => {
  resetDemo();
  const url = "/objects/obj_demo_2";
  const before = await mockRequest(url);
  const first = await mockRequest(url + "/valuation", {
    method: "POST",
    body: { area_m2: 100, wall_material: "brick", land_rub: 700000 },
  });
  assert.equal(first.full_cost_rub, 6000000);
  const card = await mockRequest(url);
  assert.equal(card.valuation_inputs.wall_material, "brick");
  assert.equal(card.valuation.full_cost_rub, 6000000);
  assert.deepEqual(card.material, before.material);
  const custom = await mockRequest(url + "/valuation", {
    method: "POST",
    body: { unit_cost_rub_m2: 30000 },
  });
  assert.equal(custom.full_cost_rub, 3000000);
  const reset = await mockRequest(url + "/valuation", {
    method: "POST",
    body: { wall_material: null, unit_cost_rub_m2: null },
  });
  assert.equal(reset.full_cost_rub, 5000000);
  assert.equal(reset.breakdown.land_rub, 700000);
});

test("demo rejects hidden materials and rounds large and fractional amounts without floating point loss", () => {
  assert.throws(() => calculateDemoValuation({}, { wall_material: "hidden" }), {
    code: "VALIDATION_ERROR",
  });
  assert.throws(() => calculateDemoValuation({}, { area_m2: 0 }), {
    code: "VALIDATION_ERROR",
  });
  const obj = { last_flight: { status: "done" }, readiness_pct: 33 };
  const result = calculateDemoValuation(obj, {
    area_m2: "99.999",
    wall_material: "aerated_concrete",
    land_rub: 700000,
  });
  assert.equal(result.full_cost_rub, 5000000);
  assert.equal(result.completed_cost_rub, 1650000);
  assert.equal(result.remaining_cost_rub, 3350000);
  assert.equal(result.value_rub, 2350000);
  const large = calculateDemoValuation(obj, {
    area_m2: 1000000,
    unit_cost_rub_m2: 10000000,
  });
  assert.equal(large.full_cost_rub, 10000000000000);
});

test("unknown cost is not displayed as zero or NaN", () => {
  const html = constructionCostPanel(
    {
      valuation: {
        is_demo: true,
        missing_data: ["area_m2", "confirmed_readiness"],
      },
    },
    helpers,
  );
  assert.equal(
    (html.match(/data-cost="[^"]+">Не рассчитано/g) || []).length,
    3,
  );
  assert.match(html, /Демонстрационные тарифы/);
  assert.match(html, /Укажите общую площадь дома/);
  assert.doesNotMatch(html, /NaN|undefined|>0 ₽</);
});

test("three construction amounts and separate land are visible", () => {
  const html = constructionCostPanel(
    {
      valuation: {
        full_cost_rub: 5000000,
        completed_cost_rub: 0,
        remaining_cost_rub: 5000000,
        breakdown: {
          area_m2: 100,
          area_source: "project_total",
          readiness_pct: 0,
          readiness_source: "employee",
          land_rub: 1000000,
        },
        explanation: "100 × 50000 = 5000000",
        range_percent: 30,
      },
      valuation_inputs: { rate_note: '<script>"x"</script>' },
    },
    helpers,
  );
  assert.match(html, /data-cost="completed_cost_rub">0 ₽/);
  assert.match(html, /Участок не включён в три суммы выше/);
  assert.match(html, /100 × 50000 = 5000000/);
  assert.match(html, /не статистическая точность/);
  assert.doesNotMatch(html, /<script>/);
});
