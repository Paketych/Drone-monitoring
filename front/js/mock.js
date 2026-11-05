// Изолированный демонстрационный адаптер. Не является движком риска.
import { calculateDemoValuation } from "./valuation-demo.js";

export const DEMO_DATE = "2026-10-31";
const pairs = (rows) =>
  rows.map(([id, label, ...rest]) => ({ id, label, ...(rest[0] || {}) }));
export const meta = {
  mock: true,
  model_version: "demo-fixtures",
  rules_version: "demo@1",
  views: pairs([
    ["top_high", "Сверху: участок"],
    ["top_low", "Сверху: дом"],
    ["N", "Север"],
    ["E", "Восток"],
    ["S", "Юг"],
    ["W", "Запад"],
  ]),
  stages: pairs([
    ["none", "Нет работ"],
    ["foundation", "Фундамент"],
    ["walls", "Стены"],
    ["roof", "Кровля"],
    ["openings", "Окна и двери"],
    ["facade", "Фасад"],
    ["done", "Готов"],
  ]).map((x, i) => ({ ...x, order: i })),
  risk_levels: pairs([
    ["green", "По плану", { color: "#247b48", order: 0 }],
    ["yellow", "Наблюдать", { color: "#946800", order: 1 }],
    ["orange", "Связаться с клиентом", { color: "#b64f08", order: 2 }],
    ["red", "Срочно", { color: "#c23738", order: 3 }],
  ]),
  readiness_levels: pairs([
    [0, "Ничего нет"],
    [50, "Фундамент, стены, возможно каркас крыши"],
    [100, "Стены, крыша, окна и двери"],
  ]),
  materials: pairs([
    ["brick", "Кирпич"],
    ["aerated_concrete", "Газобетон / пеноблок"],
    ["ceramic_block", "Керамический блок"],
    ["timber", "Брус / бревно"],
    ["frame", "Каркас"],
    ["sip", "СИП-панели"],
    ["monolith", "Монолит"],
    ["hidden", "Скрыт отделкой"],
    ["unclear", "Не определить"],
  ]),
  classes: pairs([
    ["foundation", "Фундамент", { color: "#247b48" }],
    ["walls_full", "Стены выведены", { color: "#3a72b5" }],
    ["roof_covered", "Кровля", { color: "#b64f08" }],
  ]),
  signals: pairs([
    ["LAG", "Отставание от плана"],
    ["MATERIAL_MISMATCH", "Материал не по проекту"],
    ["STALLED", "Стройка стоит"],
    ["DEADLINE_PASSED", "Срок строительства истёк"],
    ["SETBACK_SUSPECT", "Отступ от границы меньше нормы"],
  ]),
  strategies: pairs([
    ["S1", "Вылет дрона"],
    ["S2", "Реструктуризация"],
    ["S3", "Доп. финансирование на достройку ОН"],
    ["S4", "Замена обеспечения"],
    ["S5", "Мониторинг"],
    ["S6", "Передача в суд"],
    ["S7", "Регистрация ОН и оформление в залог"],
    ["S8", "Реализация ОН"],
  ]).map((x) => ({
    ...x,
    requires_confirmation: !["S1", "S5"].includes(x.id),
  })),
  steps: pairs([
    ["SMS_TERM_REMINDER", "Напоминание о плановой дате", { kind: "auto" }],
    ["FLIGHT_TASK", "Полётное задание", { kind: "auto" }],
    [
      "COMM_REASON",
      "Узнать причину незавершения",
      {
        outcomes: pairs([
          ["underfunded", "Не хватает финансирования"],
          ["hardship", "Трудная жизненная ситуация"],
          ["other", "Другая причина"],
          ["failed", "Не удалось связаться"],
        ]),
      },
    ],
    [
      "CHECK_UNDERFUNDED",
      "Неполное финансирование по договору?",
      {
        outcomes: pairs([
          ["yes", "Да, подтверждено"],
          ["no", "Нет"],
        ]),
      },
    ],
    [
      "CHECK_HARDSHIP",
      "Трудная жизненная ситуация?",
      {
        outcomes: pairs([
          ["yes", "Да, подтверждено"],
          ["no", "Нет"],
        ]),
      },
    ],
    ...["COMM_SETTLEMENT", "COMM_COLLATERAL", "COMM_PLEDGE"].map((id, i) => [
      id,
      [
        "Предложить урегулирование",
        "Предложить замену обеспечения",
        "Оформить объект в залог",
      ][i],
      {
        outcomes: pairs([
          ["agreed", "Клиент согласен"],
          ["declined", "Клиент отказался"],
        ]),
      },
    ]),
    [
      "MANUAL_INSPECTION",
      "Ручная проверка",
      {
        outcomes: pairs([
          ["confirmed", "Подтверждено"],
          ["rejected", "Не подтвердилось"],
        ]),
      },
    ],
    [
      "LISTING",
      "Размещение объекта",
      { outcomes: pairs([["done", "Размещено"]]) },
    ],
    [
      "AUCTION",
      "Торги",
      {
        outcomes: pairs([
          ["sold", "Продано"],
          ["not_sold", "Не продано"],
        ]),
      },
    ],
    ["CONFIRM_STRATEGY", "Подтвердить стратегию"],
    ["VALUATION", "AI-оценка", { kind: "auto" }],
  ]).map((x) => ({ kind: "employee", ...x })),
};
const step = (code) => ({
  code,
  label: meta.steps.find((x) => x.id === code)?.label || code,
  requires_confirmation: true,
});
const strat = (code, state = "proposed") => ({
  code,
  label: meta.strategies.find((x) => x.id === code)?.label,
  state,
});
const description = {
  1: "Строительство на раннем этапе. Дирижёр отправит напоминание о плановой дате.",
  2: "Прошла половина срока, а построен только фундамент. Платежи идут вовремя — есть время разобраться до просрочки.",
  3: "Дом построен. Нужно зарегистрировать объект и оформить его в залог.",
  4: "Строительство отстаёт. Клиент подтвердил нехватку финансирования по договору.",
  5: "Срок строительства истёк. Клиент согласен заменить обеспечение.",
  6: "Строительство идёт по плану. Отступ от границы участка требует отдельной проверки.",
  7: "Готовность 30%, осталось 10% срока, есть просроченная задолженность. Требуется решение сотрудника.",
  8: "Есть просрочка. Клиент согласен на урегулирование, трудная жизненная ситуация подтверждена.",
  9: "Уверенности недостаточно. Назначен повторный облёт; по этим снимкам тревога не создаётся.",
  10: "График соблюдается, но материал стен отличается от проекта: брус вместо газобетона. Нужна ручная проверка.",
};
function fixture(n) {
  const risks = [
    "green",
    "orange",
    "green",
    "orange",
    "orange",
    "green",
    "red",
    "orange",
    "green",
    "red",
  ];
  const stages = [
    "none",
    "foundation",
    "done",
    "walls",
    "walls",
    "openings",
    "walls",
    "roof",
    "foundation",
    "walls",
  ];
  const pcts = [0, 15, 100, 45, 45, 75, 30, 65, 15, 45];
  const plans = [20, 50, 80, 80, 100, 75, 90, 85, 40, 45];
  const remaining = [80, 50, 20, 20, -6, 25, 10, 15, 60, 55];
  const streets = [
    "Сосновая, 8",
    "Лесная, 12",
    "Центральная, 24",
    "Берёзовая, 6",
    "Садовая, 18",
    "Тихая, 4",
    "Полевая, 31",
    "Зелёная, 9",
    "Речная, 15",
    "Луговая, 22",
  ];
  const settlements = [
    "с. Марусино",
    "с. Криводановка",
    "п. Краснообск",
    "с. Каменка",
    "п. Кольцово",
    "с. Толмачёво",
    "с. Раздольное",
    "с. Барышево",
    "с. Ленинское",
    "п. Мочище",
  ];
  const scodes = [null, null, "S7", "S3", "S4", "S5", "S6", "S2", null, null];
  const nexts = [
    "SMS_TERM_REMINDER",
    "COMM_REASON",
    "CONFIRM_STRATEGY",
    "CONFIRM_STRATEGY",
    "CONFIRM_STRATEGY",
    "SMS_TERM_REMINDER",
    "CONFIRM_STRATEGY",
    "CONFIRM_STRATEGY",
    "FLIGHT_TASK",
    "MANUAL_INSPECTION",
  ];
  const hasFlight = n !== 1;
  const source = n === 4 ? "rules_fallback" : "gigachat";
  const llm = n === 2 ? "pending" : n === 4 ? "unavailable" : "accepted";
  const signal =
    n === 10
      ? "MATERIAL_MISMATCH"
      : n === 7
        ? "STALLED"
        : n === 5
          ? "DEADLINE_PASSED"
          : "LAG";
  const o = {
    id: `obj_demo_${n}`,
    address: `${settlements[n - 1]}, ул. ${streets[n - 1]}`,
    region: "Новосибирская область",
    cadastral: `54:19:000001:${10 + n}`,
    loan_id: `ИЖС-2025-${String(410 + n).padStart(4, "0")}`,
    started_at: "2025-10-27",
    planned_completion_at: n === 5 ? "2026-09-15" : "2027-10-31",
    actual_completion_at: n === 3 ? "2026-10-28" : null,
    term_left_pct: remaining[n - 1],
    overdue: [7, 8].includes(n),
    registered: n === 8,
    pledged: n === 8,
    has_aerial_photo: hasFlight,
    stage: stages[n - 1],
    readiness_pct: pcts[n - 1],
    plan_pct: plans[n - 1],
    readiness_level: n === 3 ? 100 : n === 1 ? 0 : 50,
    lag_days: Math.max(0, Math.round((plans[n - 1] - pcts[n - 1]) * 7.3)),
    risk_level: risks[n - 1],
    data_status: n === 9 ? "reshoot" : hasFlight ? "ok" : "no_data",
    loan: {
      amount: 6000000,
      dpd: n === 7 ? 18 : n === 8 ? 12 : 0,
      collateral_value_rub: 5200000,
    },
    project:
      n === 10
        ? {
            floors: 2,
            footprint_m2: 96,
            wall_material: "aerated_concrete",
            source: "Демонстрационный проект",
          }
        : null,
    checkpoints: [],
    material: hasFlight
      ? {
          project: n === 10 ? "aerated_concrete" : null,
          observed: n === 10 ? "timber" : "aerated_concrete",
          confidence: 0.86,
          match: n === 10 ? "mismatch" : "no_project",
          fixed_at_flight_id: `fl_${n}`,
          reviewed: false,
        }
      : null,
    last_flight_id: hasFlight ? `fl_${n}` : null,
    last_flight_at: hasFlight ? "2026-10-28T11:40:00+07:00" : null,
    flights: [],
    actions: [],
    next_flight_planned: n === 9 ? "2026-11-02" : "2026-11-11",
    conductor: {
      strategy: scodes[n - 1]
        ? strat(scodes[n - 1], n === 6 ? "confirmed" : "proposed")
        : null,
      next_step: step(nexts[n - 1]),
      path: [
        {
          node: "PLEDGED",
          answer: n === 8 ? "yes" : "no",
          evidence:
            n === 8 ? "Реестр: залог оформлен" : "Реестр: залог не оформлен",
        },
        {
          node: "BUILT",
          answer: n === 3 ? "yes" : "no",
          evidence: hasFlight ? `По облёту: ${pcts[n - 1]}%` : "Облёта ещё нет",
        },
      ],
      decision_source: llm === "pending" ? "rules_fallback" : source,
      llm_status: llm,
      recommendation: null,
      explanation: description[n],
    },
    risk: {
      risk_level: risks[n - 1],
      plan_pct: plans[n - 1],
      fact_pct: pcts[n - 1],
      lag_days: Math.max(0, Math.round((plans[n - 1] - pcts[n - 1]) * 7.3)),
      explanation: description[n],
      signals: ["orange", "red"].includes(risks[n - 1])
        ? [
            {
              code: signal,
              label: meta.signals.find((s) => s.id === signal).label,
              text: description[n],
            },
          ]
        : [],
      forecast: {
        completion_at: n === 6 ? "2027-09-15" : null,
        on_time: n === 6,
        basis: "from_start",
      },
    },
    land:
      n === 6
        ? {
            status: "ok",
            image_id: "demo_schematic",
            building: {
              inside_parcel: true,
              min_setback_m: 1.8,
              setback_side: "E",
              footprint_m2: 112,
            },
            vri: { code: "2.1", label: "ИЖС", allowed: true },
            improvements: { fence: true, outbuilding: true, paving: false },
            signals: [
              {
                code: "SETBACK_SUSPECT",
                text: "Отступ от восточной границы 1,8 м. Проверьте по снимку: без RTK точность ограничена.",
              },
            ],
            explanation:
              "Демонстрационная схема, не геопривязка реального снимка.",
          }
        : null,
    valuation_inputs: hasFlight ? { area_m2: 192, land_rub: 650000 } : {},
    valuation: null,
  };
  const day = 86400000,
    anchor = Date.parse(DEMO_DATE),
    left = remaining[n - 1];
  o.started_at = new Date(anchor - Math.round(730 * (1 - left / 100)) * day)
    .toISOString()
    .slice(0, 10);
  o.planned_completion_at = new Date(
    anchor + Math.round((730 * left) / 100) * day,
  )
    .toISOString()
    .slice(0, 10);
  if (hasFlight) {
    o.last_flight = makeFlight(o, `fl_${n}`);
    o.flights = [o.last_flight];
  }
  settle(o);
  return o;
}
function settle(o) {
  const c = o.conductor;
  if (c.llm_status === "accepted" && c.decision_source === "gigachat")
    c.recommendation = {
      strategy_code: c.strategy?.code || null,
      next_step_code: c.next_step?.code || null,
      reason: c.explanation,
      evidence_refs: ["readiness.level"],
      missing_data: [],
    };
}
function makeFlight(o, id) {
  return {
    flight_id: id,
    object_id: o.id,
    status: "done",
    flown_at: "2026-10-28T11:40:00+07:00",
    uploaded_at: "2026-10-28T15:00:00+07:00",
    images_count: 6,
    result: {
      stage: o.stage,
      readiness_pct: o.readiness_pct,
      readiness_level: o.readiness_level,
      confidence: o.data_status === "reshoot" ? 0.41 : 0.89,
      explanation:
        description[Number(o.id.split("_").at(-1))] ||
        "Демонстрационный результат",
      needs_reshoot: o.data_status === "reshoot",
      reshoot_reasons:
        o.data_status === "reshoot"
          ? ["Недостаточная уверенность детектора"]
          : [],
      elements: {
        foundation: { present: true, implied: true },
        walls_full: { present: o.readiness_pct >= 45, conf: 0.9 },
        roof_covered: { present: o.readiness_pct >= 65, conf: 0.87 },
      },
    },
    review: null,
    error: null,
    images: [],
  };
}
const storageKey = "sibcode.front.demo.v1";
let objects;
try {
  objects = JSON.parse(localStorage.getItem(storageKey));
} catch {
  /* storage optional */
}
if (!Array.isArray(objects) || !objects.length)
  objects = Array.from({ length: 10 }, (_, i) => fixture(i + 1));
const pending = new Map();
const imageMap = new Map();
function save() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(objects));
  } catch {
    /* UI remains usable when storage is unavailable */
  }
}
export function resetDemo() {
  objects = Array.from({ length: 10 }, (_, i) => fixture(i + 1));
  save();
}
export function demoObjects() {
  return structuredClone(objects);
}
function error(code, message) {
  const e = new Error(message);
  e.code = code;
  throw e;
}
function find(id) {
  return (
    objects.find((o) => o.id === id) || error("NOT_FOUND", "Объект не найден.")
  );
}
function refreshRegistryDemo(o) {
  const span = Date.parse(o.planned_completion_at) - Date.parse(o.started_at);
  o.term_left_pct = Math.round(
    ((Date.parse(o.planned_completion_at) - Date.parse(DEMO_DATE)) / span) *
      100,
  );
  if (o.flights.length) return;
  o.plan_pct = Math.max(0, Math.min(100, 100 - o.term_left_pct));
  o.readiness_pct = null;
  o.readiness_level = null;
  o.stage = null;
  o.risk.fact_pct = null;
  o.risk.plan_pct = o.plan_pct;
  o.risk.explanation =
    "Облёта нет: факт строительства неизвестен. Демо-шаг выбран по сроку и данным реестра.";
  o.conductor.explanation = o.risk.explanation;
  if (o.pledged && o.overdue) setNext(o, "COMM_SETTLEMENT");
  else if (!o.pledged && o.term_left_pct <= 50) {
    o.conductor.strategy = strat("S1", "confirmed");
    setNext(o, "FLIGHT_TASK");
  } else setNext(o, "SMS_TERM_REMINDER");
  settle(o);
}
function log(o, label, comment, user) {
  o.actions.unshift({
    id: `a_${Date.now()}`,
    created_at: new Date().toISOString(),
    author: user || "Сотрудник",
    label,
    comment,
  });
  save();
}
function setNext(o, code) {
  o.conductor.next_step = code ? step(code) : null;
}
function propose(o, code) {
  o.conductor.strategy = strat(code);
  setNext(o, "CONFIRM_STRATEGY");
  o.conductor.explanation = `Предложена стратегия «${o.conductor.strategy.label}». Требуется подтверждение сотрудника.`;
}
export function applyStep(o, code, outcome, comment = "", user = "Сотрудник") {
  if (o.conductor.next_step?.code !== code)
    error(
      "STEP_NOT_EXPECTED",
      "Этот шаг больше не ожидается. Обновите карточку.",
    );
  if (
    !meta.steps
      .find((s) => s.id === code)
      ?.outcomes?.some((x) => x.id === outcome)
  )
    error("VALIDATION_ERROR", "Выберите допустимый исход шага.");
  if (code === "COMM_REASON") {
    if (outcome === "underfunded") setNext(o, "CHECK_UNDERFUNDED");
    else if (outcome === "hardship") setNext(o, "CHECK_HARDSHIP");
    else if (outcome === "failed") {
      if (o.term_left_pct < 0) setNext(o, "COMM_COLLATERAL");
      else if (o.overdue) propose(o, "S6");
      else setNext(o, "COMM_COLLATERAL");
    } else setNext(o, "MANUAL_INSPECTION");
  } else if (code === "CHECK_UNDERFUNDED") {
    if (outcome === "yes") propose(o, "S3");
    else setNext(o, "MANUAL_INSPECTION");
  } else if (code === "CHECK_HARDSHIP")
    propose(o, outcome === "yes" ? "S2" : "S6");
  else if (code === "COMM_COLLATERAL") {
    if (outcome === "agreed") propose(o, "S4");
    else setNext(o, "MANUAL_INSPECTION");
  } else if (code === "COMM_SETTLEMENT")
    setNext(o, outcome === "agreed" ? "CHECK_HARDSHIP" : "MANUAL_INSPECTION");
  else if (code === "COMM_PLEDGE") {
    if (outcome === "agreed") propose(o, "S7");
    else setNext(o, "MANUAL_INSPECTION");
  } else if (code === "MANUAL_INSPECTION") {
    setNext(o, outcome === "confirmed" ? "COMM_REASON" : null);
    if (outcome === "rejected") {
      o.risk_level = "green";
      o.risk.risk_level = "green";
      o.risk.signals = [];
      o.risk.explanation = "Ручная проверка: нарушение не подтвердилось.";
    }
  } else if (code === "LISTING") setNext(o, "AUCTION");
  else if (code === "AUCTION") {
    if (outcome === "sold") propose(o, "S8");
    else setNext(o, "LISTING");
  }
  o.conductor.path.push({
    node: code,
    answer: outcome,
    evidence: comment || "Исход внесён сотрудником",
  });
  o.conductor.llm_status = "accepted";
  o.conductor.decision_source = "gigachat";
  settle(o);
  log(
    o,
    `${meta.steps.find((s) => s.id === code).label}: ${meta.steps.find((s) => s.id === code).outcomes.find((x) => x.id === outcome).label}`,
    comment,
    user,
  );
  return o.conductor;
}
const list = (items, p) => {
  const page = Number(p.get("page") || 1),
    size = Number(p.get("page_size") || 20);
  return {
    items: items.slice((page - 1) * size, page * size),
    total: items.length,
    page,
    page_size: size,
  };
};
function refreshDemoValuation(o) {
  if (!Object.hasOwn(o, "valuation_inputs")) {
    const old = o.valuation?.breakdown;
    o.valuation_inputs =
      old?.footprint_m2 && old?.floors
        ? {
            area_m2: old.footprint_m2 * old.floors,
            land_rub: old.land_rub ?? null,
          }
        : {};
  }
  o.valuation = calculateDemoValuation(o);
}

export async function mockRequest(path, { method = "GET", body, user } = {}) {
  const u = new URL(path, "http://demo"),
    p = u.searchParams;
  const segments = u.pathname.split("/").filter(Boolean);
  let [entity, id, part] = segments;
  await new Promise((r) => setTimeout(r, 100));
  if (entity === "meta") return structuredClone(meta);
  if (entity === "objects" && !id && method === "GET") {
    let rows = objects
      .filter((o) =>
        ["risk_level", "stage", "data_status"].every(
          (k) => !p.get(k) || o[k] === p.get(k),
        ),
      )
      .filter(
        (o) =>
          !p.get("strategy") ||
          o.conductor.strategy?.code === p.get("strategy"),
      )
      .filter((o) =>
        ["overdue", "pledged"].every(
          (k) => !p.get(k) || String(o[k]) === p.get(k),
        ),
      )
      .filter(
        (o) =>
          !p.get("q") ||
          `${o.address} ${o.cadastral} ${o.loan_id}`
            .toLowerCase()
            .includes(p.get("q").toLowerCase()),
      );
    rows.sort(
      (a, b) =>
        meta.risk_levels.find((r) => r.id === b.risk_level).order -
          meta.risk_levels.find((r) => r.id === a.risk_level).order ||
        b.lag_days - a.lag_days,
    );
    if (p.get("sort") === "address")
      rows.sort((a, b) => a.address.localeCompare(b.address, "ru"));
    if (p.get("sort") === "-lag_days")
      rows.sort((a, b) => b.lag_days - a.lag_days);
    return structuredClone(list(rows, p));
  }
  if (entity === "objects" && !id && method === "POST") {
    if (
      !body.address ||
      !body.cadastral ||
      !body.started_at ||
      !body.planned_completion_at
    )
      error("VALIDATION_ERROR", "Заполните обязательные поля.");
    if (body.planned_completion_at < body.started_at)
      error(
        "VALIDATION_ERROR",
        "Плановая дата раньше даты начала строительства.",
      );
    if (objects.some((o) => o.cadastral === body.cadastral))
      error("VALIDATION_ERROR", "Объект с этим кадастровым номером уже есть.");
    const o = fixture(1);
    Object.assign(o, body, {
      id: `obj_${Date.now()}_${objects.length}`,
      conductor: { ...o.conductor },
      risk: { ...o.risk },
      actions: [],
    });
    o.term_left_pct = Math.round(
      ((Date.parse(o.planned_completion_at) - Date.parse(DEMO_DATE)) /
        (Date.parse(o.planned_completion_at) - Date.parse(o.started_at))) *
        100,
    );
    refreshRegistryDemo(o);
    objects.push(o);
    save();
    return structuredClone(o);
  }
  if (entity === "objects" && id && id !== "import") {
    const o = find(id);
    refreshDemoValuation(o);
    if (!part) return structuredClone({ ...o, strategy: o.conductor.strategy });
    if (part === "history")
      return {
        plan: [
          { date: o.started_at, pct: 0 },
          { date: o.planned_completion_at, pct: 100 },
        ],
        fact: o.flights
          .filter((f) => f.status === "done")
          .map((f) => ({
            flight_id: f.flight_id,
            flown_at: f.flown_at,
            stage: f.review?.stage || f.result.stage,
            readiness_pct: f.review?.readiness_pct ?? f.result.readiness_pct,
            readiness_level: f.result.readiness_level,
            reviewed: !!f.review,
            risk_level: o.risk_level,
          })),
        forecast: o.risk.forecast,
        steps: o.actions.map((a) => ({
          date: a.created_at,
          label: a.label,
          comment: a.comment,
        })),
      };
    if (part === "actions") return structuredClone({ items: o.actions });
    if (part === "risk") return structuredClone(o.risk);
    if (part === "conductor") {
      if (o.conductor.llm_status === "pending") {
        const count = (pending.get(id) || 0) + 1;
        pending.set(id, count);
        if (count >= 2) {
          o.conductor.llm_status = "accepted";
          o.conductor.decision_source = "gigachat";
          settle(o);
          save();
        }
      }
      return structuredClone(o.conductor);
    }
    if (part === "steps") {
      applyStep(o, body.step, body.outcome, body.comment, user);
      return structuredClone(o.conductor);
    }
    if (part === "strategy") {
      if (
        o.conductor.strategy?.state !== "proposed" ||
        o.conductor.strategy.code !== body.strategy
      )
        error(
          "STRATEGY_NOT_PROPOSED",
          "Предложение изменилось. Обновите карточку.",
        );
      if (body.decision === "reject" && !body.comment?.trim())
        error("VALIDATION_ERROR", "Укажите причину отклонения.");
      o.conductor.strategy.state =
        body.decision === "confirm" ? "confirmed" : "rejected";
      setNext(
        o,
        body.decision === "confirm" && body.strategy === "S6"
          ? "LISTING"
          : null,
      );
      log(
        o,
        `${body.decision === "confirm" ? "Подтверждена" : "Отклонена"} стратегия: ${o.conductor.strategy.label}`,
        body.comment,
        user,
      );
      return structuredClone(o.conductor);
    }
    if (part === "valuation") {
      if (method === "POST") {
        const parameters = { ...o.valuation_inputs, ...body };
        const result = calculateDemoValuation(o, parameters);
        o.valuation_inputs = parameters;
        o.valuation = result;
        log(o, "Пересчитана стоимость постройки", "", user);
      }
      return structuredClone(o.valuation);
    }
    if (part === "flights" && method === "GET")
      return structuredClone({ items: o.flights });
    if (part === "flights" && method === "POST") {
      const files = body.getAll("files");
      const views = JSON.parse(body.get("views") || "[]");
      if (views.length !== files.length || views.some((v) => !v))
        error(
          "VIEWS_REQUIRED",
          "В демо укажите ракурс для каждого файла. EXIF анализирует бэкенд.",
        );
      const fid = `fl_${Date.now()}`,
        f = makeFlight(o, fid);
      f.status = "queued";
      f.result = null;
      f.uploaded_at = new Date().toISOString();
      f.flown_at = body.get("flown_at") || new Date().toISOString();
      f.images_count = files.length;
      f.ready_at = Date.now() + 3000;
      const imgs = files.map((file, i) => ({
        id: `${fid}_${i}`,
        view: views[i],
        url: URL.createObjectURL(file),
        thumb_url: null,
        detections: [],
        view_source: "manual",
        quality_ok: null,
      }));
      imageMap.set(fid, imgs);
      o.flights.unshift(f);
      o.last_flight = f;
      o.last_flight_id = fid;
      o.last_flight_at = f.flown_at;
      o.data_status = "processing";
      save();
      return { flight_id: fid, status: "queued" };
    }
  }
  if (entity === "flights") {
    const o = objects.find((x) => x.flights.some((f) => f.flight_id === id));
    if (!o) error("NOT_FOUND", "Облёт не найден.");
    const f = o.flights.find((x) => x.flight_id === id);
    if (!part) {
      if (["queued", "processing"].includes(f.status)) {
        f.status = Date.now() >= f.ready_at ? "done" : "processing";
        if (f.status === "done") {
          f.result = makeFlight(o, id).result;
          o.data_status = "review";
          save();
        }
      }
      return structuredClone(f);
    }
    if (part === "images") return imageMap.get(id) || [];
    if (part === "reshoot") {
      o.data_status = "reshoot";
      log(o, "Назначена пересъёмка", body?.comment, user);
      return { status: "reshoot" };
    }
    if (part === "review") {
      if (f.status !== "done")
        error("FLIGHT_NOT_READY", "Дождитесь обработки облёта.");
      f.review = {
        ...body,
        id: `rev_${Date.now()}`,
        author: user,
        created_at: new Date().toISOString(),
      };
      if (body.action === "correct") {
        if (
          !meta.stages.some((s) => s.id === body.stage) ||
          !Number.isFinite(body.readiness_pct) ||
          body.readiness_pct < 0 ||
          body.readiness_pct > 100
        )
          error("VALIDATION_ERROR", "Проверьте стадию и процент готовности.");
        o.stage = body.stage;
        o.readiness_pct = body.readiness_pct;
        o.risk.fact_pct = body.readiness_pct;
        o.lag_days = Math.max(
          0,
          Math.round((o.plan_pct - body.readiness_pct) * 7.3),
        );
        o.risk.lag_days = o.lag_days;
      }
      o.data_status = "ok";
      log(
        o,
        body.action === "correct"
          ? "Исправлен результат облёта"
          : "Результат облёта принят",
        body.comment,
        user,
      );
      return structuredClone(f.review);
    }
    if (part === "act")
      return {
        flight_id: id,
        loan_id: o.loan_id,
        inspected_at: f.flown_at,
        fields: [
          {
            id: "address",
            label: "Адрес объекта",
            value: o.address,
            source: "registry",
          },
          {
            id: "type",
            label: "Тип объекта",
            value: "Жилой дом (ИЖС)",
            source: "registry",
          },
          {
            id: "wall_material",
            label: "Материал наружных стен",
            value:
              meta.materials.find((x) => x.id === o.material?.observed)
                ?.label || null,
            source: "drone",
          },
          {
            id: "area",
            label: "Площадь, м²",
            value: o.project?.footprint_m2 || null,
            source: o.project ? "project" : "employee",
          },
          {
            id: "rooms",
            label: "Количество комнат",
            value: null,
            source: "employee",
          },
          {
            id: "partitions",
            label: "Материал перегородок",
            value: null,
            source: "employee",
          },
          {
            id: "floors",
            label: "Этажность",
            value: o.project?.floors || null,
            source: o.project ? "project" : "employee",
          },
          {
            id: "ownership",
            label: "Документ о праве собственности",
            value: null,
            source: "employee",
          },
          {
            id: "land_right",
            label: "Вид права на земельный участок",
            value: null,
            source: "employee",
          },
        ],
        defects:
          o.readiness_pct < 65
            ? ["Кровля отсутствует", "Окна и двери не установлены"]
            : [],
        condition_suggested:
          o.readiness_pct === 100 ? "satisfactory" : "unsatisfactory",
        condition: null,
        inspector: null,
        images: [],
      };
  }
  if (entity === "alerts") {
    if (method === "GET") {
      let items = objects
        .filter((o) => o.risk.signals.length && !o.alert_closed)
        .map((o) => ({
          id: `al_${o.id}`,
          object_id: o.id,
          address: o.address,
          risk_level: o.risk_level,
          signals: o.risk.signals,
          overdue: o.overdue,
          state: o.alert_progress ? "in_progress" : "open",
          recommended_action: o.conductor.next_step,
        }));
      if (p.get("risk_level"))
        items = items.filter((a) => a.risk_level === p.get("risk_level"));
      if (p.get("state"))
        items = items.filter((a) => a.state === p.get("state"));
      items.sort(
        (a, b) =>
          meta.risk_levels.find((r) => r.id === b.risk_level).order -
          meta.risk_levels.find((r) => r.id === a.risk_level).order,
      );
      return structuredClone(list(items, p));
    }
    const o = find(id.replace(/^al_/, ""));
    if (body.decision === "reject" && !body.outcome?.trim())
      error("VALIDATION_ERROR", "Укажите причину ложной тревоги.");
    if (body.decision === "reject" || body.close_alert) o.alert_closed = true;
    else o.alert_progress = true;
    log(
      o,
      body.decision === "reject" ? "Тревога отклонена" : "Мера принята",
      body.outcome,
      user,
    );
    return { ok: true };
  }
  if (entity === "tasks") {
    let rows = objects
      .filter(
        (o) =>
          o.conductor.next_step &&
          meta.steps.find((s) => s.id === o.conductor.next_step.code)?.kind ===
            "employee",
      )
      .map((o) => ({
        id: `st_${o.id}`,
        object_id: o.id,
        address: o.address,
        risk_level: o.risk_level,
        step: o.conductor.next_step,
        strategy: o.conductor.strategy,
        created_at: "2026-10-28T12:00:00+07:00",
      }));
    for (const k of ["risk_level", "strategy", "step"])
      if (p.get(k))
        rows = rows.filter(
          (r) => (k === "risk_level" ? r[k] : r[k]?.code) === p.get(k),
        );
    return structuredClone(list(rows, p));
  }
  error(
    "NOT_IMPLEMENTED",
    "Эта операция отсутствует в демонстрационном адаптере.",
  );
}
export async function importDemoRows(rows, user) {
  let created = 0,
    updated = 0;
  const errors = [];
  for (const [i, body] of rows.entries()) {
    try {
      if (
        !body.address ||
        !body.cadastral ||
        !body.started_at ||
        !body.planned_completion_at
      )
        error("VALIDATION_ERROR", "Не заполнены обязательные поля.");
      if (body.planned_completion_at < body.started_at)
        error(
          "VALIDATION_ERROR",
          "Плановая дата раньше даты начала строительства.",
        );
      const o = objects.find((x) => x.cadastral === body.cadastral);
      if (o) {
        Object.assign(o, body);
        refreshRegistryDemo(o);
        updated++;
        log(o, "Данные реестра обновлены", "", user);
      } else {
        await mockRequest("/objects", { method: "POST", body, user });
        created++;
      }
    } catch (e) {
      errors.push({
        row: body._row || i + 2,
        code: e.code || "VALIDATION_ERROR",
        message: e.message,
      });
    }
  }
  save();
  return { created, updated, errors };
}
