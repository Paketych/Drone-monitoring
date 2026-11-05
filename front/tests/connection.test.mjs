import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeMeta,
  supports,
  selectableMaterials,
  percent,
} from "../js/capabilities.js";
import {
  materialPanel,
  readinessDetail,
  materialConfidenceText,
} from "../js/inspection.js";
import { analysisPanel } from "../js/analysis.js";

test("Inspection connects without bank dictionaries", () => {
  const meta = normalizeMeta({
    scope: "inspection",
    materials: [],
    views: [],
    capabilities: {
      readiness: true,
      material_review: true,
      alerts: false,
      tasks: false,
    },
  });
  assert.equal(supports(meta, "readiness"), true);
  assert.equal(supports(meta, "alerts"), false);
  assert.deepEqual(meta.steps, []);
  assert.equal(
    supports(
      normalizeMeta({ materials: [], views: [], risk_levels: [], steps: [] }),
      "conductor",
    ),
    true,
  );
});

test("Display values are excluded from editable materials and missing confidence is explicit", () => {
  assert.deepEqual(
    selectableMaterials({
      materials: [{ id: "brick" }, { id: "hidden" }, { id: "unclear" }],
    }),
    [{ id: "brick" }],
  );
  assert.equal(percent(null), "Не указана");
  assert.equal(percent(undefined), "Не указана");
  assert.equal(percent(0), "0%");
});

const ui = {
  esc: String,
  btn: (text) => text,
  label: (group, value) => value,
  date: (value) => value || "—",
};

test("Material and wall confidence remain distinct on crop cards", () => {
  const observation = {
    cls: "walls_full",
    conf: 0.6,
    detector_confidence: 0.6,
    material: "brick",
    material_conf: 0.96,
    confidence: 0.96,
    crop_url: "/crop.jpg",
    view: "N",
  };
  assert.equal(
    materialConfidenceText(observation),
    "Материал: 96% · Стена: 60%",
  );
  const html = materialPanel(
    {
      last_flight: {
        status: "done",
        material_result: {
          material: "brick",
          material_label: "Кирпич",
          status: "needs_review",
          is_demo: false,
          per_view: [observation],
        },
      },
    },
    { capabilities: {} },
    ui,
  );
  assert.match(html, /Материал: 96% · Стена: 60%/);
  assert.doesNotMatch(html, /Уверенность 60%/);
});

test("Confidence captions support the ML format, legacy crops and unclassified tiny walls", () => {
  assert.equal(
    materialConfidenceText({
      cls: "walls_full",
      conf: 0.6,
      material_conf: 0.96,
    }),
    "Материал: 96% · Стена: 60%",
  );
  assert.equal(materialConfidenceText({ conf: 0.91 }), "Материал: 91%");
  assert.equal(
    materialConfidenceText({ confidence: 0.92, material_conf: null }),
    "Материал: 92%",
  );
  assert.equal(
    materialConfidenceText({
      cls: "walls_full",
      material_conf: null,
      confidence: 0,
      detector_confidence: 0.9,
    }),
    "Материал: Не указана · Стена: 90%",
  );
});
test("Unfixed material remains visible", () => {
  const html = materialPanel(
    {
      material: null,
      last_flight: {
        status: "done",
        material_result: {
          status: "needs_review",
          material: "timber",
          material_label: "Брус",
          confidence: 0.7,
          is_demo: false,
          explanation: "Проверить дом",
          per_view: [],
        },
      },
    },
    { capabilities: { material_review: true } },
    ui,
  );
  assert.match(html, /Брус/);
  assert.match(html, /needs_review/);
  assert.match(html, /70%/);
  assert.doesNotMatch(html, /NaN/);
});
test("Manual readiness takes precedence over unavailable model", () => {
  const html = readinessDetail(
    {
      last_flight: {
        status: "done",
        readiness_review: {
          stage: "walls",
          readiness_pct: 45,
          bank_readiness: 50,
          author: "inspector",
          comment: "Проверено",
        },
        result: {
          status: "model_unavailable",
          is_demo: true,
          stage: null,
          readiness_pct: null,
        },
      },
    },
    ui,
  );
  assert.match(html, /45%/);
  assert.match(html, /Проверено сотрудником/);
});

test("AI text is escaped and pending requests cannot be submitted again", () => {
  const report = {
    llm_status: "pending",
    explanation: '<img src=x onerror="alert(1)">',
    connection: { mock: true },
    evidence_refs: [],
    facts: {},
  };
  const html = analysisPanel(report, {
    esc: (value) => String(value).replaceAll("<", "&lt;"),
    btn: (text, action, cls, icon, attrs) =>
      `<button ${attrs}>${text}</button>`,
  });
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /disabled/);
  assert.match(html, /Действия не выполняются автоматически/);
});
