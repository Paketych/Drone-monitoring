import { icon } from "./icons.js";
import {
  request,
  settings,
  saveSettings,
  allObjects,
  importRegistry,
} from "./api.js";
import { DEMO_DATE, resetDemo } from "./mock.js";
import { readRegistry } from "./import.js";
import { progressChart } from "./chart.js";
import {
  normalizeMeta,
  supports,
  selectableMaterials,
} from "./capabilities.js";
import {
  inspectionOverview,
  readinessDetail,
  materialPanel,
  materialConfidenceText,
} from "./inspection.js";
import { analysisPanel } from "./analysis.js";
import { constructionCostPanel } from "./valuation.js";

const root = document.querySelector("#app"),
  dialog = document.querySelector("#dialog");
let meta,
  objects = [],
  alerts = [],
  tasks = [],
  currentObject = null,
  routeToken = 0,
  pollTimer,
  uploadFiles = [],
  importFile = null,
  importRows = [],
  objectTab = "overview";
let filters = {
    risk_level: "",
    strategy: "",
    stage: "",
    data_status: "",
    overdue: "",
    pledged: "",
    q: "",
    sort: "-risk",
    page: 1,
  },
  alertFilter = "all";
const stageStates = {
  upcoming: "Впереди",
  met: "Выполнено",
  missed: "Срок пропущен",
  unverified: "Не подтверждено",
};
const dataStates = {
  ok: ["check", "Данные проверены"],
  review: ["shield", "Нужна проверка"],
  reshoot: ["refresh", "На пересъёмку"],
  processing: ["clock", "Обрабатывается"],
  no_data: ["image", "Нет результата"],
};
const strategyStates = {
  waiting: "Ожидает шага",
  proposed: "Ждёт подтверждения",
  confirmed: "Подтверждена",
  rejected: "Отклонена",
  done: "Завершена",
};
const sources = {
  registry: "Реестр",
  project: "Проект",
  drone: "Дрон",
  employee: "Сотрудник",
};
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const label = (group, id) =>
  meta?.[group]?.find((x) => String(x.id) === String(id))?.label ||
  id ||
  "Не указано";
const color = (id) => {
  const c = meta?.risk_levels?.find((r) => r.id === id)?.color;
  return /^#[\da-f]{3,8}$/i.test(c || "") ? c : "#64748b";
};
const badge = (id) =>
  id
    ? `<span class="badge" style="--risk-color:${color(id)}"><span class="dot"></span>${esc(label("risk_levels", id))}</span>`
    : '<span class="subtle">Риск не оценён</span>';
const dataBadge = (id) =>
  `<span class="data-state ${["review", "reshoot"].includes(id) ? "needs-attention" : ""}">${icon((dataStates[id] || dataStates.no_data)[0])}${esc((dataStates[id] || dataStates.no_data)[1])}</span>`;
const date = (value, opts = {}) =>
  value
    ? new Date(value).toLocaleDateString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        timeZone: "Asia/Barnaul",
        ...opts,
      })
    : "—";
const money = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      }).format(value);
const listItems = (r) => (Array.isArray(r) ? r : r?.items || []);
const btn = (text, action, cls = "", ico = "", attrs = "") =>
  `<button class="btn ${cls}" data-action="${action}" ${attrs}>${ico ? icon(ico) : ""}${text}</button>`;
const objectLink = (id, text, cls = "text-link") =>
  `<a class="${cls}" href="#/objects/${encodeURIComponent(id)}"${text.startsWith("<svg") ? ' aria-label="Открыть объект"' : ""}>${text}</a>`;
const empty = (title, text, ico = "check") =>
  `<div class="empty">${icon(ico)}<h2>${title}</h2><p>${text}</p></div>`;
function toast(message) {
  const e = document.querySelector("#toast");
  e.textContent = message;
  e.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => e.classList.remove("visible"), 4500);
}
function showDialog(title, body, { wide = false } = {}) {
  dialog.className = wide ? "wide" : "";
  dialog.innerHTML = `<div class="dialog-head"><h2 id="dialog-title">${title}</h2>${btn('<span class="sr-only">Закрыть</span>', "close-dialog", "ghost icon-button", "close")}</div><div class="dialog-body">${body}</div>`;
  if (!dialog.open) dialog.showModal();
}
function formError(form, message) {
  let e = form.querySelector(".form-error");
  if (!e) {
    e = document.createElement("div");
    e.className = "form-error";
    e.setAttribute("role", "alert");
    form.prepend(e);
  }
  e.textContent = message;
  e.tabIndex = -1;
  e.focus();
}
async function submit(form, callback) {
  const button = form.querySelector('[type="submit"]');
  if (button?.disabled) return;
  if (button) {
    button.disabled = true;
    button.dataset.original = button.innerHTML;
    button.innerHTML = '<span class="spinner"></span> Сохраняем…';
  }
  form.querySelector(".form-error")?.remove();
  try {
    await callback();
  } catch (e) {
    formError(form, e.message);
  } finally {
    if (button?.isConnected) {
      button.disabled = false;
      button.innerHTML = button.dataset.original;
    }
  }
}
async function refresh() {
  [objects, alerts, tasks] = await Promise.all([
    allObjects(),
    supports(meta, "alerts")
      ? request("/alerts?page_size=100").then(listItems)
      : [],
    supports(meta, "tasks")
      ? request("/tasks?page_size=100").then(listItems)
      : [],
  ]);
}
function navigation() {
  const route = location.hash.split("/")[1] || "overview";
  const name = settings().display_name || settings().user;
  return `<aside class="sidebar" id="sidebar" aria-label="Основная навигация">
  <a href="#/overview" class="brand" aria-label="Дрон-мониторинг — главная"><img class="brand-logo" src="assets/favicon.svg" alt="" aria-hidden="true"><span class="brand-name">Дрон-мониторинг</span></a>
  <p class="product-name">Контроль строительства ИЖС</p><nav class="nav">${[
    ["overview", "overview", "Обзор мониторинга"],
    ["objects", "house", "Объекты"],
    ["tasks", "tasks", "Задачи"],
    ["flights", "drone", "Облёты"],
  ]
    .filter(([key]) => key !== "tasks" || supports(meta, "tasks"))
    .map(
      ([key, ic, title]) =>
        `<a href="#/${key}" class="${route === key ? "active" : ""}" ${route === key ? 'aria-current="page"' : ""}>${icon(ic)}${title}${key === "tasks" ? `<span class="nav-count">${tasks.length}</span>` : ""}</a>`,
    )
    .join("")}</nav>
  <div class="sidebar-bottom">${btn("Как это работает", "help", "ghost", "help")}${btn("Подключение и профиль", "settings", "ghost", "settings")}<div class="system-status"><strong><span class="dot" style="color:${meta.mock ? "#ad7819" : "#168149"}"></span>${settings().mode === "demo" ? "Демонстрационный режим" : meta.mock ? "Модели не подключены" : "Подключено к API"}</strong>${settings().mode === "demo" ? "Синтетические данные для демо" : meta.mock ? "Фотографии сохраняются, модели не подключены" : "Данные получены с сервера"}</div></div></aside>
  <div class="main-wrap"><header class="topbar"><div class="row">${btn('<span class="sr-only">Открыть меню</span>', "menu", "ghost icon-button mobile-menu", "menu")}<div class="breadcrumb"><span>Мониторинг ИЖС</span>${icon("chevron")}<strong>${esc({ overview: "Рабочий стол", objects: "Объекты", tasks: "Задачи", flights: "Облёты", import: "Импорт реестра", new: "Новый объект", upload: "Загрузка облёта", act: "Акт осмотра" }[route] || "Объект")}</strong></div></div><div class="topbar-right"><form id="global-search" class="searchbox" role="search">${icon("search")}<input name="q" aria-label="Поиск объекта" placeholder="Адрес, кадастр или кредит"></form>${settings().mode === "demo" ? `<span class="demo-tag">${icon("info")}Демо-данные</span>` : '<span class="live-status">Данные API</span>'}<button class="btn ghost user-block" data-action="settings" aria-label="Профиль: ${esc(name)}"><span>${esc(name)}</span><span class="avatar">${esc(
    name
      .split(" ")
      .map((s) => s[0])
      .slice(0, 2)
      .join(""),
  )}</span></button></div></header><main id="main" tabindex="-1" class="content"></main></div>`;
}
function footer() {
  return `<footer class="page-footer"><span>Дрон-мониторинг ИЖС · Прототип</span><span>${settings().mode === "demo" ? "Демонстрационный срез на 31 октября 2026" : "Данные подключённого API"} · ${esc(meta.rules_version || "")}</span></footer>`;
}
function heading(title, subtitle, actions = "") {
  return `<div class="page-header"><div><h1>${title}</h1>${subtitle ? `<p>${subtitle}</p>` : ""}</div><div class="header-actions">${actions}</div></div>`;
}
function renderPortfolio() {
  const risks = [...(meta.risk_levels || [])].sort((a, b) => b.order - a.order);
  return `<section class="portfolio" aria-labelledby="portfolio-title"><div class="portfolio-top"><div class="portfolio-title"><h2 id="portfolio-title">Портфель под наблюдением</h2><span>${objects.length} объектов</span></div><a class="text-link" href="#/objects">Весь портфель ${icon("arrow")}</a></div><div class="risk-track" aria-hidden="true">${risks
    .map((r) => {
      const count = objects.filter((o) => o.risk_level === r.id).length;
      return count
        ? `<span style="flex:${count};background:${color(r.id)}"></span>`
        : "";
    })
    .join("")}</div><div class="risk-stats">${risks
    .map((r) => {
      const count = objects.filter((o) => o.risk_level === r.id).length;
      return `<button class="risk-stat" data-action="risk-filter" data-risk="${esc(r.id)}"><span class="risk-stat-top"><span class="dot" style="color:${color(r.id)}"></span>${esc(r.label)}</span><span class="risk-stat-count">${count}<small>объект${count === 1 ? "" : count > 1 && count < 5 ? "а" : "ов"}</small></span></button>`;
    })
    .join("")}</div></section>`;
}
function alertCard(a) {
  const o = objects.find((o) => o.id === a.object_id);
  return `<article class="alert-item"><div class="alert-top">${badge(a.risk_level)}<span class="subtle">${a.overdue ? "Есть просрочка" : "Платежи без просрочки"}</span></div><h3 class="signal-title">${esc(a.signals?.[0]?.label || "Требует внимания")}</h3><div class="object-address">${icon("location")}${objectLink(a.object_id, esc(a.address), "table-link")}</div><p class="alert-copy">${esc(a.signals?.[0]?.text || "Откройте карточку для подробностей.")}</p>${o && o.readiness_pct != null ? `<div class="mini-comparison"><span>По плану<strong>${o.plan_pct ?? "—"}%</strong></span><span>По облёту<strong>${o.readiness_pct}%</strong></span>${o.lag_days ? `<span class="lag">Отставание ${o.lag_days} дн.</span>` : ""}</div>` : ""}<div class="alert-actions"><div class="action-buttons">${a.state === "in_progress" ? '<span class="badge">Мера принята</span>' : btn("Принять меру", "accept-alert", "primary small", "", `data-id="${esc(a.id)}" data-object="${esc(a.object_id)}"`)}${btn("Ложная тревога", "reject-alert", "ghost small", "", `data-id="${esc(a.id)}"`)}</div>${objectLink(a.object_id, `Открыть объект ${icon("arrow")}`)}</div></article>`;
}
function compactTask(t) {
  return `<article class="task-item"><span class="task-symbol">${icon(t.step.code === "COMM_REASON" ? "phone" : t.step.code === "CONFIRM_STRATEGY" ? "shield" : "tasks")}</span><div class="task-content"><h3>${esc(t.step.label)}</h3><p>${esc(t.address)}</p>${objectLink(t.object_id, t.strategy ? `Рассмотреть ${esc(t.strategy.code)} ${icon("arrow")}` : `Перейти к задаче ${icon("arrow")}`)}</div></article>`;
}
function overview() {
  if (!supports(meta, "risk"))
    return inspectionOverview(objects, inspectionHelpers());
  const filtered = alerts.filter(
    (a) =>
      alertFilter === "all" ||
      (alertFilter === "urgent"
        ? a.risk_level === "red"
        : a.state === "in_progress"),
  );
  const urgent = objects
    .filter((o) => ["red", "orange"].includes(o.risk_level))
    .slice(0, 4);
  return (
    heading(
      "Обзор мониторинга",
      "Выявляйте риски раньше просрочки и выбирайте следующий шаг.",
      `<span class="date-label">${icon("clock")}${meta.mock ? "31 октября 2026" : date(new Date())}</span>${btn("Загрузить облёт", "choose-upload", "primary", "upload")}`,
    ) +
    renderPortfolio() +
    strategySummary() +
    `<div class="dashboard-grid"><section class="panel"><div class="panel-head"><div class="section-heading"><h2>Требуют внимания</h2><span class="count">${alerts.length}</span></div><span class="subtle">Сначала срочные</span></div><div class="tabs" role="tablist" aria-label="Фильтр тревог">${[
      ["all", "Все тревоги"],
      ["urgent", "Срочные"],
      ["progress", "В работе"],
    ]
      .map(
        ([id, text]) =>
          `<button role="tab" aria-selected="${alertFilter === id}" class="${alertFilter === id ? "active" : ""}" data-action="alert-tab" data-tab="${id}">${text}</button>`,
      )
      .join(
        "",
      )}</div>${filtered.length ? filtered.map(alertCard).join("") : empty("Всё разобрано", "В этой категории открытых тревог нет.")}</section><aside class="stack dashboard-side"><section class="panel"><div class="panel-head"><div class="section-heading"><h2>Ждут решения</h2><span class="count">${tasks.length}</span></div><a class="text-link" href="#/tasks">Все ${icon("arrow")}</a></div>${tasks.slice(0, 4).map(compactTask).join("") || empty("Задач нет", "Когда потребуется ваше решение, оно появится здесь.")}<div class="panel-foot">Решение сотрудника запускает следующий шаг дирижёра.</div></section><div class="note">${icon("shield")}<div><strong>Помогаем до появления просрочки</strong>Отставание строительства — повод связаться с клиентом, пока есть время подобрать решение.</div></div></aside></div><section class="panel worklist"><div class="panel-head"><h2>Объекты в фокусе</h2><a href="#/objects" class="text-link">Все объекты ${icon("arrow")}</a></div><div class="table-wrap"><table><thead><tr><th>Объект</th><th>Риск</th><th>Готовность / план</th><th>Следующий шаг</th><th></th></tr></thead><tbody>${urgent.map((o) => `<tr><td>${objectLink(o.id, `<strong>${esc(o.address)}</strong>`, "table-link")}<p class="subtle">${esc(o.loan_id || o.cadastral)}</p></td><td>${badge(o.risk_level)}</td><td><div class="progress-pair"><span>${o.readiness_pct ?? "—"}% / ${o.plan_pct ?? "—"}%</span><span class="progress-line"><span style="width:${Math.max(0, Math.min(100, o.readiness_pct || 0))}%"></span></span></div></td><td>${esc(o.next_step?.label || o.conductor?.next_step?.label || "Ожидание")}</td><td>${objectLink(o.id, icon("chevron"), "table-link")}</td></tr>`).join("")}</tbody></table></div></section>`
  );
}
function strategySummary() {
  return `<div class="strategy-summary" aria-label="Количество объектов по стратегиям"><span>По стратегиям</span>${meta.strategies
    .map((s) => {
      const count = objects.filter(
        (o) => (o.strategy || o.conductor?.strategy)?.code === s.id,
      ).length;
      return `<button class="strategy-count" data-action="strategy-filter" data-strategy="${esc(s.id)}" title="${esc(s.label)}">${esc(s.id)} <strong>${count}</strong></button>`;
    })
    .join("")}</div>`;
}
function options(group, value, placeholder) {
  return (
    `<option value="">${placeholder}</option>` +
    (group === "materials" ? selectableMaterials(meta) : meta[group] || [])
      .map(
        (s) =>
          `<option value="${esc(s.id)}" ${String(value) === String(s.id) ? "selected" : ""}>${esc(s.label)}</option>`,
      )
      .join("")
  );
}
async function objectsPage() {
  if (!supports(meta, "risk")) return inspectionObjectsPage();
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters))
    if (v !== "" && v != null) p.set(k, v);
  p.set("page_size", "10");
  const result = await request(`/objects?${p}`);
  return (
    heading(
      "Объекты",
      "Реестр строительства, сигналы риска и текущие стратегии.",
      `${btn("Импорт реестра", "go-import", "", "upload")}${btn("Добавить объект", "go-new", "primary", "plus")}`,
    ) +
    `<section class="panel"><div class="panel-head"><h2>Объекты мониторинга <span class="count">${result.total}</span></h2>${btn("Сбросить фильтры", "reset-filters", "ghost small", "refresh")}</div><form id="filters" class="filters"><label class="filter-search">${icon("search")}<input class="field" name="q" value="${esc(filters.q)}" placeholder="Адрес, кадастр или кредит" aria-label="Поиск в реестре"></label><select name="risk_level" aria-label="Уровень риска">${options("risk_levels", filters.risk_level, "Все риски")}</select><select name="strategy" aria-label="Стратегия">${options("strategies", filters.strategy, "Все стратегии")}</select><select name="data_status" aria-label="Состояние данных"><option value="">Все данные</option>${Object.entries(
      dataStates,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${filters.data_status === k ? "selected" : ""}>${v[1]}</option>`,
      )
      .join(
        "",
      )}</select><select name="stage" aria-label="Стадия">${options("stages", filters.stage, "Все стадии")}</select><select name="overdue" aria-label="Просроченная задолженность"><option value="">ПЗ: все</option><option value="true" ${filters.overdue === "true" ? "selected" : ""}>Есть ПЗ</option><option value="false" ${filters.overdue === "false" ? "selected" : ""}>Нет ПЗ</option></select><select name="pledged" aria-label="Залог"><option value="">Залог: все</option><option value="true" ${filters.pledged === "true" ? "selected" : ""}>В залоге</option><option value="false" ${filters.pledged === "false" ? "selected" : ""}>Не в залоге</option></select><button type="submit" class="btn small">Найти</button></form>${
      result.items.length
        ? `<div class="table-wrap"><table class="loan-table"><thead><tr><th><button class="sort-btn" data-action="sort" data-sort="address">Объект ${icon("down")}</button></th><th>Риск</th><th>Стратегия</th><th>Осталось срока</th><th>Факт / план</th><th>Шкала банка</th><th><button class="sort-btn" data-action="sort" data-sort="-lag_days">Отставание ${icon("down")}</button></th><th>ПЗ / залог</th><th>Последний облёт</th><th>Данные</th></tr></thead><tbody>${result.items
            .map((o) => {
              const s = o.strategy || o.conductor?.strategy;
              return `<tr><td>${objectLink(o.id, `<strong>${esc(o.address)}</strong>`, "table-link")}<p class="subtle">${esc(o.loan_id || o.cadastral)}</p></td><td>${badge(o.risk_level)}</td><td>${s ? `<strong>${esc(s.code)}</strong><p class="subtle">${esc(s.label)}</p>` : '<span class="muted">Не выбрана</span>'}</td><td>${o.term_left_pct < 0 ? "Срок истёк" : `${o.term_left_pct ?? "—"}%`}</td><td>${o.readiness_pct ?? "—"}% / ${o.plan_pct ?? "—"}%<p class="subtle">${esc(label("stages", o.stage))}</p></td><td><span title="${esc(label("bank_readiness", o.bank_readiness))}">${o.bank_readiness ?? "—"}</span></td><td>${o.lag_days ? `${o.lag_days} дн.` : "—"}</td><td>${o.overdue ? "Есть ПЗ" : "Нет ПЗ"}<p class="subtle">${o.pledged ? "В залоге" : "Не в залоге"}</p></td><td>${date(o.last_flight_at)}</td><td>${dataBadge(o.data_status)}</td></tr>`;
            })
            .join("")}</tbody></table></div>`
        : empty(
            "Объекты не найдены",
            "Измените поиск или сбросьте фильтры.",
            "search",
          )
    }<div class="table-pagination"><span>${result.total ? `${(filters.page - 1) * 10 + 1}–${Math.min(filters.page * 10, result.total)} из ${result.total}` : "0 объектов"}</span><div class="row">${btn("Назад", "page-prev", "small", "back", filters.page <= 1 ? "disabled" : "")}<span>Стр. ${filters.page}</span>${btn("Далее", "page-next", "small", "chevron", filters.page * 10 >= result.total ? "disabled" : "")}</div></div></section>`
  );
}
function tasksPage() {
  return (
    heading(
      "Задачи",
      "Исходы коммуникаций, проверки и стратегии, которые ждут вашего решения.",
    ) +
    `<section class="panel"><div class="panel-head"><h2>Ждут сотрудника <span class="count">${tasks.length}</span></h2><select id="task-risk" aria-label="Фильтр задач по риску">${options("risk_levels", "", "Все риски")}</select></div><div id="task-rows">${taskRows(tasks)}</div></section>`
  );
}
function taskRows(rows) {
  return rows.length
    ? `<div class="table-wrap"><table class="task-table"><thead><tr><th>Задача и объект</th><th>Риск</th><th>Стратегия</th><th>Действие</th></tr></thead><tbody>${rows.map((t) => `<tr><td><strong>${esc(t.step.label)}</strong><p class="subtle">${esc(t.address)}</p></td><td>${badge(t.risk_level)}</td><td>${esc(t.strategy?.label || "После уточнения данных")}</td><td>${objectLink(t.object_id, `Открыть ${icon("arrow")}`)}</td></tr>`).join("")}</tbody></table></div>`
    : empty("Все задачи выполнены", "Новых решений сотрудника не требуется.");
}
function flightsPage() {
  const rows = objects.filter((o) => o.last_flight_id);
  return (
    heading(
      "Облёты",
      "Состояние обработки, проверка результатов и повторная съёмка.",
      btn("Загрузить облёт", "choose-upload", "primary", "upload"),
    ) +
    `<section class="panel"><div class="panel-head"><h2>Последние облёты <span class="count">${rows.length}</span></h2></div>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Объект</th><th>Дата облёта</th><th>Стадия</th><th>Готовность</th><th>Состояние</th><th></th></tr></thead><tbody>${rows.map((o) => `<tr><td>${objectLink(o.id, `<strong>${esc(o.address)}</strong>`, "table-link")}<p class="subtle">${esc(o.last_flight_id)}</p></td><td>${date(o.last_flight_at)}</td><td>${esc(label("stages", o.stage))}</td><td>${o.readiness_pct ?? "—"}%</td><td>${dataBadge(o.data_status)}</td><td>${objectLink(o.id, "Посмотреть")}</td></tr>`).join("")}</tbody></table></div>` : empty("Облётов пока нет", "Загрузите снимки для объекта.", "drone")}</section>`
  );
}
function strategyBlock(o) {
  const c = o.conductor,
    s = c?.strategy;
  if (!c)
    return empty(
      "Решение ещё не готово",
      "Дирижёр пока не вернул рекомендацию.",
      "clock",
    );
  const source =
    c.decision_source === "llm"
      ? "AI · рекомендация проверена"
      : c.llm_status === "pending"
        ? "Готовится AI-рекомендация"
        : "Правила банка · резервное решение";
  const next = c.next_step,
    stepMeta = meta.steps.find((x) => x.id === next?.code);
  return `<section class="panel" id="strategy"><div class="panel-head"><h2>Стратегия и следующий шаг</h2>${icon("bolt")}</div><div class="panel-body"><div class="ai-label">${c.llm_status === "pending" ? '<span class="spinner"></span>' : icon("shield")}${source}</div><h3 class="strategy-title">${s ? `${esc(s.code)} · ${esc(s.label)}` : next ? esc(next.label) : "Ожидание следующего события"}</h3>${s ? `<span class="badge">${esc(strategyStates[s.state] || s.state)}</span>` : ""}<p class="strategy-copy" style="margin-top:12px">${esc(c.recommendation?.reason || c.explanation || "Объяснение не предоставлено.")}</p>${c.llm_status === "pending" ? '<p class="subtle" style="margin-top:10px">Резерв по правилам уже доступен. Риск рассчитан по правилам сервиса.</p>' : ""}${c.recommendation?.missing_data?.length ? `<div class="note warning" style="margin-top:15px">${icon("info")}<div>Не хватает данных: ${esc(c.recommendation.missing_data.join(", "))}</div></div>` : ""}
  ${s?.state === "proposed" ? `<form id="strategy-form" class="strategy-next"><label for="strategy-comment">Решение сотрудника</label><textarea id="strategy-comment" name="comment" placeholder="Комментарий к решению"></textarea><div class="action-buttons"><button class="btn primary" type="submit" name="decision" value="confirm">${icon("check")}Подтвердить</button><button class="btn" type="submit" name="decision" value="reject">Отклонить</button></div><p class="subtle" style="margin-top:10px">Проверка ответа AI не заменяет ваше подтверждение.</p></form>` : next && stepMeta?.outcomes?.length ? `<form id="step-form" class="strategy-next" data-step="${esc(next.code)}"><label>Следующий шаг: ${esc(next.label)}</label><div class="step-options">${stepMeta.outcomes.map((x) => `<label><input type="radio" name="outcome" value="${esc(x.id)}" required>${esc(x.label)}</label>`).join("")}</div><label for="step-comment">Комментарий</label><textarea id="step-comment" name="comment" placeholder="Что выяснили и на чём основано решение"></textarea><button class="btn primary" type="submit">Сохранить исход ${icon("arrow")}</button></form>` : next ? `<div class="strategy-next"><p class="subtle">${stepMeta?.kind === "auto" ? "Автоматический шаг" : "Следующий шаг"}: ${esc(next.label)}</p></div>` : ""}
  <details><summary>Путь решения и доказательства</summary><ol class="decision-path">${(c.path || []).map((x) => `<li><strong>${esc(label("steps", x.node))} · ${esc({ yes: "Да", no: "Нет", expired: "Срок истёк" }[x.answer] || x.answer)}</strong>${esc(x.evidence)}</li>`).join("")}</ol></details></div></section>`;
}
function overviewDetail(o, history) {
  const r = o.risk || {},
    f = o.last_flight,
    result = f?.result,
    review = f?.review;
  const readiness =
    review?.readiness_pct ?? result?.readiness_pct ?? r.fact_pct;
  return `<div class="detail-grid"><div class="stack"><section class="panel"><div class="panel-head"><h2>План против факта</h2><div class="chart-legend"><span><i class="legend-line"></i>План</span><span><i class="legend-line fact"></i>Облёт</span></div></div><div class="chart-container">${progressChart(history, esc)}</div><div class="comparison-grid"><div class="comparison-cell"><p>По плану на дату облёта</p><strong>${r.plan_pct ?? o.plan_pct ?? "—"}%</strong><h3>Шкала банка — по факту</h3><small>План ${o.checkpoints?.length ? "по контрольным точкам" : "линейный от начала до срока"}</small></div><div class="comparison-cell"><p>По последнему облёту</p><strong>${readiness ?? "—"}%</strong><h3>${esc(label("stages", review?.stage || result?.stage || o.stage))}</h3><small>Шкала банка: ${result?.bank_readiness ?? r.bank_readiness ?? "—"} · ${review ? `Исправлено: ${esc(review.author)}, ${date(review.created_at)}` : "Результат модели"}</small></div></div><div class="panel-foot">${r.forecast?.completion_at ? `Прогноз завершения: ${date(r.forecast.completion_at)}.` : "Прогноз даты завершения отсутствует."} ${r.forecast?.basis === "from_start" ? "Грубый прогноз по одному облёту от даты начала." : ""} Плановая дата: ${date(o.planned_completion_at)}.</div></section>
  ${o.checkpoints?.length ? `<section class="panel"><div class="panel-head"><h2>Контрольные точки</h2></div><div class="table-wrap"><table><thead><tr><th>Стадия</th><th>Срок</th><th>Состояние</th></tr></thead><tbody>${o.checkpoints.map((c) => `<tr><td>${esc(label("stages", c.stage))}</td><td>${date(c.due)}</td><td>${esc(stageStates[c.state] || "Не проверено")}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
  <section class="panel"><div class="panel-head"><h2>Последний облёт</h2>${dataBadge(o.data_status)}</div><div class="panel-body">${
    result
      ? `<dl class="facts"><dt>Дата съёмки</dt><dd>${date(f.flown_at)}</dd><dt>Уверенность модели</dt><dd>${Math.round(result.confidence * 100)}%</dd><dt>Шкала банка</dt><dd>${result.bank_readiness} / 100</dd><dt>Следующий облёт</dt><dd>${date(o.next_flight_planned)}</dd></dl><p class="strategy-copy" style="margin-top:17px">${esc(result.explanation)}</p>${review ? `<div class="note neutral" style="margin-top:15px">${icon("info")}<div><strong>Результат проверен: ${esc(review.author)}</strong>${date(review.created_at)}. ${esc(review.comment || "")}<details><summary>Исходный результат модели</summary><p>${esc(label("stages", result.stage))} · ${result.readiness_pct}%</p></details></div></div>` : ""}${result.reshoot_reasons?.length ? `<div class="note warning" style="margin-top:15px">${icon("alert")}<div>${esc(result.reshoot_reasons.join("; "))}</div></div>` : ""}<details><summary>Найденные элементы</summary><dl class="facts" style="margin-top:15px">${Object.entries(
          result.elements || {},
        )
          .map(
            ([k, v]) =>
              `<dt>${esc(label("classes", k))}</dt><dd>${v.implied ? "Подразумевается" : v.present ? "Найден" : "Не найден"} ${v.conf ? `· ${Math.round(v.conf * 100)}%` : ""}</dd>`,
          )
          .join(
            "",
          )}</dl></details><div class="action-buttons" style="margin-top:20px">${btn("Согласен", "review-accept", "primary small", "check")}${btn("Исправить", "review-correct", "small")}${btn("На пересъёмку", "reshoot", "small", "refresh")}<a href="#/act/${esc(f.flight_id)}" class="btn small">${icon("file")}Акт осмотра</a></div>`
      : f && ["queued", "processing"].includes(f.status)
        ? `<div class="processing"><span class="spinner"></span><div><h3>Облёт обрабатывается</h3><p>Результат появится автоматически.</p></div></div>`
        : empty(
            "Облёта ещё нет",
            "Загрузите снимки, чтобы сравнить план и факт.",
            "drone",
          )
  }${meta.mock ? '<p class="subtle" style="margin-top:16px">В демо результат задан заранее. Загруженные фотографии доступны для просмотра, но не анализируются.</p>' : ""}</div></section></div><div class="stack">${strategyBlock(o)}<section class="panel"><div class="panel-head"><h2>Кредит и строительство</h2></div><div class="panel-body"><dl class="facts"><dt>Сумма кредита</dt><dd>${money(o.loan?.amount)}</dd><dt>Начало строительства</dt><dd>${date(o.started_at)}</dd><dt>Плановое завершение</dt><dd>${date(o.planned_completion_at)}</dd><dt>Осталось срока</dt><dd>${o.term_left_pct < 0 ? "Срок истёк" : `${o.term_left_pct ?? "—"}%`}</dd><dt>Просроченная задолженность</dt><dd>${o.overdue ? "Есть" : "Нет"}</dd><dt>Залог</dt><dd>${o.pledged ? "Оформлен" : "Не оформлен"}</dd><dt>Регистрация</dt><dd>${o.registered ? "Зарегистрирован" : "Не зарегистрирован"}</dd></dl></div></section></div></div>`;
}
async function photosDetail(o) {
  const images = o.last_flight
    ? await request(`/flights/${o.last_flight.flight_id}/images`)
    : [];
  window.__photos = images;
  return `<section class="panel section-block"><div class="panel-head"><h2>Галерея ракурсов</h2><span>${images.length} снимков</span></div><div class="panel-body"><div class="gallery">${images.map((im) => `<button class="photo-tile" data-action="view-photo" data-id="${esc(im.id)}"><img src="${esc(im.quality_ok == null ? im.url : im.thumb_url || im.url)}" alt="${esc(im.filename || "Снимок объекта")}"><span class="photo-caption">${esc(im.view ? label("views", im.view) : "Ракурс не указан")}</span></button>`).join("")}</div>${images.length ? "" : "<p>Фотографии пока не загружены.</p>"}</div></section>`;
}

async function materialDetail(o) {
  if (!supports(meta, "conductor"))
    return materialPanel(o, meta, inspectionHelpers());
  const m = o.material,
    views = o.last_flight?.material_result?.per_view || [];
  const images = views.length
    ? await request(`/flights/${o.last_flight.flight_id}/images`)
    : [];
  const crops = views
    .map((v) => {
      const im = images.find((x) => x.id === v.image_id),
        b = v.crop_bbox;
      if (!im || !b || b.length !== 4) return "";
      const [x1, y1, x2, y2] = b.map((x) =>
        Math.max(0, Math.min(1, Number(x))),
      );
      const w = x2 - x1,
        h = y2 - y1;
      if (!(w > 0 && h > 0)) return "";
      return `<div><div class="wall-crop" style="aspect-ratio:${(w * (im.width || 1)) / (h * (im.height || 1))}"><img src="${esc(im.url)}" alt="Кроп стены: ${esc(label("views", v.view))}" style="width:${100 / w}%;left:${(-x1 / w) * 100}%;top:${(-y1 / h) * 100}%"></div><p style="margin-top:8px">${esc(label("views", v.view))} · ${esc(label("materials", v.class))}</p><p class="subtle">${materialConfidenceText(v)}</p></div>`;
    })
    .join("");
  return `<div class="section-block panel"><div class="panel-head"><h2>Материал стен</h2>${m ? `<span class="badge">Уверенность ${Math.round((m.confidence || 0) * 100)}%</span>` : ""}</div><div class="panel-body">${m ? `<div class="comparison-grid" style="border-top:0"><div class="comparison-cell"><p>По проекту</p><h3>${m.project ? esc(label("materials", m.project)) : "Проект не загружен"}</h3></div><div class="comparison-cell"><p>По облёту</p><h3>${esc(label("materials", m.observed))}</h3><small>Закреплён по облёту ${esc(m.fixed_at_flight_id)}</small></div></div>${m.match === "mismatch" ? `<div class="risk-explanation" style="--risk-color:${color("red")};margin-top:18px">${icon("alert")}<div><strong>Материал не соответствует проекту</strong>Подтвердите расхождение после проверки снимков.<div class="action-buttons" style="margin-top:12px">${btn("Подтвердить", "material-confirm", "small primary")}${btn("Ложная тревога", "material-reject", "small")}</div></div></div>` : ""}${crops ? `<div class="gallery" style="margin-top:20px">${crops}</div>` : '<p class="subtle" style="margin-top:18px">Фрагменты фасадов появятся после обработки снимков открытых стен.</p>'}` : empty("Материал не определён", "Дождитесь облёта со снимками открытых стен.", "image")}</div></div>`;
}
function landDetail(o) {
  const l = o.land;
  return `<section class="panel section-block"><div class="panel-head"><h2>Освоение участка</h2></div><div class="panel-body">${l ? `${meta.mock && l.status === "ok" ? `<figure class="land-figure" style="margin:0 0 20px"><svg viewBox="0 0 400 230" role="img" aria-label="Демонстрационная схема участка с домом и отступом 1,8 метра"><path d="M40 25H360V205H40Z" fill="#e2efe2" stroke="#739a7c" stroke-width="1.5" stroke-dasharray="6 4"/><path d="M185 75H330V160H185Z" fill="#bad4c0" stroke="#648e70" stroke-width="1.5"/><path d="M330 110H360M330 103v14m30-14v14" stroke="#b25b1f" stroke-width="1.5"/><text x="258" y="120" text-anchor="middle" fill="#365d40" font-size="14">Дом</text><text x="346" y="95" text-anchor="middle" fill="#9b4a16" font-size="12">1,8 м</text><text x="80" y="190" fill="#53775b" font-size="12">Участок</text></svg><figcaption class="subtle">Схема демо-объекта. Не фотография и не результат геопривязки.</figcaption></figure>` : ""}<dl class="facts"><dt>Состояние проверки</dt><dd>${esc({ ok: "Результат получен", unverified: "Не подтверждено", no_parcel: "Нет границ участка" }[l.status] || l.status)}</dd><dt>Дом внутри участка</dt><dd>${l.building?.inside_parcel == null ? "Не определено" : l.building.inside_parcel ? "Да" : "Нет"}</dd><dt>Ближайшая граница</dt><dd>${l.building?.min_setback_m != null ? `${l.building.min_setback_m} м` : "Не определено"}</dd><dt>ВРИ</dt><dd>${esc(l.vri?.label || "Не указан")}</dd><dt>Забор / хозпостройки / дорожки</dt><dd>${l.improvements ? [l.improvements.fence, l.improvements.outbuilding, l.improvements.paving].map((v) => (v == null ? "Неизвестно" : v ? "Есть" : "Нет")).join(" / ") : "Нет данных"}</dd></dl><p class="strategy-copy" style="margin-top:16px">${esc(l.explanation)}</p>${(l.signals || []).map((s) => `<div class="note warning" style="margin-top:15px">${icon("alert")}<div><strong>На проверку</strong>${esc(s.text)}<p>Этот сигнал не меняет уровень риска клиента.</p></div></div>`).join("")}` : empty("Проверка участка недоступна", "Нужны границы участка и верхний снимок с данными для геопривязки.", "location")}</div></section>`;
}
function valuationDetail(o) {
  return constructionCostPanel(o, inspectionHelpers());
}
async function journalDetail(o) {
  const actions = listItems(await request(`/objects/${o.id}/actions`));
  return `<section class="panel section-block"><div class="panel-head"><h2>Журнал действий</h2><span class="subtle">Кто, когда и на основании чего</span></div><div class="panel-body">${actions.length ? actions.map((a) => `<article class="journal-item"><h3>${esc(a.label || a.action_code || a.step || a.decision || "Действие")}</h3><p>${esc(a.comment || a.outcome || a.result || "")}</p><small>${esc(a.author || a.user || "Система")} · ${date(a.created_at, { hour: "2-digit", minute: "2-digit" })}</small></article>`).join("") : empty("История пока пуста", "Сохранённые исходы и решения появятся здесь.", "clock")}</div></section>`;
}

async function inspectionObjectsPage() {
  const params = new URLSearchParams({ page: filters.page, page_size: 10 });
  for (const key of ["q", "stage", "data_status", "overdue", "pledged"])
    if (filters[key] !== "") params.set(key, filters[key]);
  params.set("sort", filters.sort === "address" ? "address" : "-created_at");
  const result = await request(`/objects?${params}`);
  return (
    heading(
      "Объекты",
      "Материалы и готовность по фотографиям.",
      btn("Добавить объект", "go-new", "primary", "plus"),
    ) +
    `<section class="panel"><div class="panel-head"><h2>Объекты мониторинга: ${result.total}</h2></div><form id="filters" class="filters"><label>Поиск<input class="field" name="q" value="${esc(filters.q)}" aria-label="Поиск в реестре"></label><select name="stage" aria-label="Стадия">${options("stages", filters.stage, "Все стадии")}</select><select name="data_status" aria-label="Состояние данных"><option value="">Все данные</option>${Object.entries(
      dataStates,
    )
      .map(
        ([id, value]) =>
          `<option value="${id}" ${filters.data_status === id ? "selected" : ""}>${value[1]}</option>`,
      )
      .join(
        "",
      )}</select><button class="btn" type="submit">Найти</button>${btn("Сбросить", "reset-filters", "ghost")}</form><div class="table-wrap"><table><thead><tr><th>Объект</th><th>Стадия</th><th>Готовность</th><th>Шкала банка</th><th>Материал</th><th>Последний облёт</th><th>Данные</th></tr></thead><tbody>${result.items.map((o) => `<tr><td>${objectLink(o.id, esc(o.address))}<p class="subtle">${esc(o.cadastral || "")}</p></td><td>${o.stage ? esc(label("stages", o.stage)) : "—"}</td><td>${o.readiness_pct ?? "—"}%</td><td>${o.bank_readiness ?? "—"}</td><td>${o.material ? esc(label("materials", o.material.observed)) : "Не закреплён"}</td><td>${date(o.last_flight_at)}</td><td>${dataBadge(o.data_status)}</td></tr>`).join("")}</tbody></table></div><div class="panel-foot">Страница ${filters.page}${filters.page > 1 ? btn("Назад", "page-prev", "small") : ""}${filters.page * 10 < result.total ? btn("Далее", "page-next", "small") : ""}</div></section>`
  );
}

function inspectionHelpers() {
  return { heading, btn, objectLink, esc, dataBadge, date, label, money };
}
async function inspectionObjectPage(id) {
  const o = await request(`/objects/${encodeURIComponent(id)}`);
  currentObject = o;
  if (supports(meta, "llm_analysis"))
    o.ai_analysis = await request(
      `/objects/${encodeURIComponent(id)}/analysis`,
    );
  const tabs = [
    ["overview", "Обзор"],
    ["photos", "Снимки"],
    ["material", "Материал стен"],
  ];
  if (supports(meta, "valuation"))
    tabs.push(["valuation", "Стоимость постройки"]);
  if (supports(meta, "actions")) tabs.push(["journal", "Журнал"]);
  if (!tabs.some(([key]) => key === objectTab)) objectTab = "overview";
  let body;
  if (objectTab === "photos") body = await photosDetail(o);
  else if (objectTab === "material") body = await materialDetail(o);
  else if (objectTab === "valuation")
    body = constructionCostPanel(o, inspectionHelpers());
  else if (objectTab === "journal") body = await journalDetail(o);
  else {
    body = readinessDetail(o, inspectionHelpers());
    if (o.ai_analysis)
      body += analysisPanel(o.ai_analysis, inspectionHelpers());
    if (supports(meta, "history")) {
      const history = await request(
        `/objects/${encodeURIComponent(id)}/history`,
      );
      if (history.fact.length)
        body += `<section class="panel section-block"><div class="panel-head"><h2>План и история готовности</h2></div><div class="chart-container">${progressChart(history, esc)}</div></section>`;
    }
  }
  schedulePoll(o);
  return (
    `<a href="#/objects" class="back-link">К списку объектов</a>` +
    heading(
      esc(o.address),
      esc(o.cadastral || "Кадастровый номер не указан"),
      btn("Загрузить облёт", "upload-object", "primary", "upload"),
    ) +
    `<div class="tabs detail-tabs" role="tablist" aria-label="Разделы объекта">${tabs.map(([key, title]) => `<button role="tab" aria-selected="${objectTab === key}" class="${objectTab === key ? "active" : ""}" data-action="object-tab" data-tab="${key}">${title}</button>`).join("")}</div>${body}`
  );
}
function materialCorrectionDialog() {
  showDialog(
    "Проверка материала стен",
    `<form id="material-review-form"><label for="manual-material">Материал стен</label><select id="manual-material" name="material" required>${options("materials", currentObject.material?.observed || "", "Выберите материал")}</select><label for="material-comment">Основание исправления</label><textarea id="material-comment" name="comment" required></textarea><button type="submit" class="btn primary">Сохранить материал</button></form>`,
  );
}

async function objectPage(id) {
  if (!supports(meta, "conductor")) return inspectionObjectPage(id);
  const o = await request(`/objects/${encodeURIComponent(id)}`);
  currentObject = o;
  const history = await request(`/objects/${encodeURIComponent(id)}/history`);
  const r = o.risk || {};
  const tabs = [
    ["overview", "Обзор"],
    ["photos", "Снимки"],
    ["material", "Материал стен"],
    ["land", "Участок"],
    ["valuation", "Стоимость постройки"],
    ["journal", "Журнал"],
  ];
  let body;
  if (objectTab === "photos") body = await photosDetail(o);
  else if (objectTab === "material") body = await materialDetail(o);
  else if (objectTab === "land") body = landDetail(o);
  else if (objectTab === "valuation") body = valuationDetail(o);
  else if (objectTab === "journal") body = await journalDetail(o);
  else body = overviewDetail(o, history);
  schedulePoll(o);
  return (
    `<a href="#/objects" class="back-link">${icon("back")}К списку объектов</a>` +
    heading(
      esc(o.address),
      esc(o.region || "Объект индивидуального жилищного строительства"),
      btn("Загрузить облёт", "upload-object", "primary", "upload"),
    ) +
    `<div class="object-header"><div class="object-info"><span>Кадастр: <strong>${esc(o.cadastral)}</strong></span><span>Кредит: <strong>${esc(o.loan_id || "Не указан")}</strong></span><span>${o.pledged ? "Залог оформлен" : "Залог не оформлен"}</span>${dataBadge(o.data_status)}</div></div><div class="risk-explanation" style="--risk-color:${color(r.risk_level)}">${icon(r.risk_level === "green" ? "check" : "alert")}<div><strong>${esc(label("risk_levels", r.risk_level))}</strong>${esc(r.explanation || "Оценка риска ещё не сформирована.")}</div></div><div class="tabs detail-tabs" role="tablist" aria-label="Разделы объекта">${tabs.map(([key, title]) => `<button role="tab" aria-selected="${objectTab === key}" class="${objectTab === key ? "active" : ""}" data-action="object-tab" data-tab="${key}">${title}</button>`).join("")}</div>${body}`
  );
}
function schedulePoll(o) {
  clearTimeout(pollTimer);
  if (
    o.conductor?.llm_status === "pending" ||
    o.ai_analysis?.llm_status === "pending" ||
    ["queued", "processing"].includes(o.last_flight?.status)
  ) {
    const token = routeToken;
    pollTimer = setTimeout(async () => {
      if (token !== routeToken || document.hidden || dialog.open) {
        schedulePoll(o);
        return;
      }
      try {
        if (o.ai_analysis?.llm_status === "pending") {
          const summary = await request(`/objects/${o.id}/analysis`);
          if (
            summary.llm_status !== "pending" ||
            summary.analysis_id !== o.ai_analysis.analysis_id
          ) {
            await render(true);
            return;
          }
        }
        if (o.conductor?.llm_status === "pending") {
          const c = await request(`/objects/${o.id}/conductor`);
          if (c.llm_status !== o.conductor.llm_status) {
            await render(true);
            return;
          }
        }
        if (["queued", "processing"].includes(o.last_flight?.status)) {
          const f = await request(`/flights/${o.last_flight.flight_id}`);
          if (f.status === "done" || f.status === "failed") {
            await refresh();
            await render();
            return;
          }
        }
        schedulePoll(o);
      } catch (e) {
        toast(e.message);
      }
    }, 2000);
  }
}

function newObjectPage() {
  return (
    `<a href="#/objects" class="back-link">${icon("back")}К списку объектов</a>` +
    heading("Добавить объект", "Обязательные данные — из реестра банка.") +
    `<section class="panel form-width"><div class="panel-head"><h2>Данные объекта</h2></div><form id="new-object" class="panel-body"><div class="form-grid">${[
      ["address", "Адрес", "text", true],
      ["cadastral", "Кадастровый номер", "text", true],
      ["loan_id", "Номер кредитного договора", "text", false],
      ["started_at", "Дата начала строительства", "date", true],
      ["planned_completion_at", "Плановое завершение", "date", true],
      ["actual_completion_at", "Фактическое завершение", "date", false],
    ]
      .map(
        ([name, title, type, required]) =>
          `<div class="form-group"><label for="new-${name}">${title}${required ? " *" : ""}</label><input id="new-${name}" class="field" name="${name}" type="${type}" ${required ? "required" : ""}></div>`,
      )
      .join("")}<div class="form-group full"><div class="row wrap">${[
      ["overdue", "Есть просрочка"],
      ["registered", "Объект зарегистрирован"],
      ["pledged", "Залог оформлен"],
      ["has_aerial_photo", "Есть фото с неба"],
    ]
      .map(
        ([name, title]) =>
          `<label class="check-label"><input type="checkbox" name="${name}">${title}</label>`,
      )
      .join(
        "",
      )}</div></div></div><details><summary>Дополнительно: проект и график стадий</summary><div class="form-grid" style="margin-top:18px"><div class="form-group"><label for="project-material">Материал стен по проекту</label><select name="wall_material" id="project-material">${options("materials", "", "Не указан")}</select></div><div class="form-group"><label for="project-floors">Этажность</label><input class="field" id="project-floors" name="floors" type="number" min="1" max="10"></div><div class="form-group"><label for="project-area">Площадь застройки, м²</label><input class="field" id="project-area" name="footprint_m2" type="number" min="1" step="0.1"></div><div class="form-group"><label for="project-total-area">Общая площадь дома, м²</label><input class="field" id="project-total-area" name="total_area_m2" type="number" min="0.001" max="1000000" step="0.001"></div><div class="form-group"><label for="project-source">Источник проекта</label><input class="field" id="project-source" name="project_source" placeholder="Документ, лист"></div><div class="form-group full"><label>Контрольные точки</label><div id="checkpoint-rows"></div>${btn("Добавить контрольную точку", "add-checkpoint", "small", "plus")}</div></div></details><div class="form-actions"><a href="#/objects" class="btn">Отмена</a><button type="submit" class="btn primary">Добавить объект</button></div></form></section>`
  );
}
function importPage() {
  importFile = null;
  importRows = [];
  return (
    `<a href="#/objects" class="back-link">${icon("back")}К списку объектов</a>` +
    heading(
      "Импорт реестра",
      "Загрузите XLSX или CSV в формате банка. Повторный импорт обновит объект по кадастру.",
    ) +
    `<section class="panel form-width"><div class="panel-head"><h2>Реестр объектов</h2></div><div class="panel-body"><div class="note neutral">${icon("file")}<div>Нужны колонки: территория, кадастровый номер, плановая и фактическая даты, ПЗ, регистрация, залог, дата начала, наличие фото с неба.</div></div><form id="import-form" style="margin-top:22px"><label class="dropzone" id="import-drop">${icon("upload")}<strong>Выберите реестр или перетащите сюда</strong><p>XLSX или CSV · до 10 МБ · первая строка — заголовки</p><input type="file" id="import-file" accept=".xlsx,.csv" aria-label="Выберите реестр XLSX или CSV"></label><div id="import-preview"></div><div class="form-actions"><a href="#/objects" class="btn">Отмена</a><button type="submit" class="btn primary" id="import-submit" disabled>Импортировать</button></div></form><div id="import-result"></div></div></section>`
  );
}
async function importSelected(file) {
  const form = document.querySelector("#import-form"),
    button = document.querySelector("#import-submit"),
    preview = document.querySelector("#import-preview");
  if (!file || !form) return;
  button.disabled = true;
  form.querySelector(".form-error")?.remove();
  preview.innerHTML =
    '<div class="boot" style="min-height:90px"><span class="spinner"></span>Читаем реестр…</div>';
  try {
    importRows = await readRegistry(file);
    importFile = file;
    preview.innerHTML = `<div class="file-summary">${icon("file")}<div><strong>${esc(file.name)}</strong><p class="subtle">${importRows.length} строк к импорту · ${(file.size / 1024).toFixed(1)} КБ</p></div></div><div class="table-wrap"><table><thead><tr><th>Адрес</th><th>Кадастр</th><th>Плановая дата</th></tr></thead><tbody>${importRows
      .slice(0, 3)
      .map(
        (r) =>
          `<tr><td>${esc(r.address)}</td><td>${esc(r.cadastral)}</td><td>${date(r.planned_completion_at)}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div><p class="subtle" style="margin-top:10px">Первые ${Math.min(3, importRows.length)} строки. Итог валидации будет показан после импорта.</p>`;
    button.disabled = false;
  } catch (e) {
    preview.innerHTML = "";
    importFile = null;
    importRows = [];
    formError(form, e.message);
  }
}
async function uploadPage(id) {
  const o = await request(`/objects/${encodeURIComponent(id)}`);
  currentObject = o;
  for (const f of uploadFiles) URL.revokeObjectURL(f.url);
  uploadFiles = [];
  return (
    `<a href="#/objects/${esc(id)}" class="back-link">${icon("back")}К объекту</a>` +
    heading("Загрузить облёт", esc(o.address)) +
    `<section class="panel form-width"><div class="panel-head"><h2>Снимки объекта</h2></div><div class="panel-body"><div class="note neutral">${icon("drone")}<div><strong>Рекомендуются шесть ракурсов</strong>Север, восток, юг, запад, дом сверху и участок сверху. Формат JPG / PNG, до 30 МБ на снимок. Можно загрузить от 1 до 20 файлов.</div></div>${settings().mode === "demo" ? '<p class="subtle" style="margin-top:14px">В демо заранее подготовленный результат появится через 3 секунды. Фото можно просмотреть, но AI их здесь не анализирует. Укажите ракурсы вручную.</p>' : ""}<form id="upload-form" style="margin-top:20px"><label class="dropzone" id="flight-drop">${icon("upload")}<strong>Перетащите фотографии сюда</strong><p>Или нажмите, чтобы выбрать файлы</p><input id="flight-files" type="file" multiple accept="image/jpeg,image/png" aria-label="Фотографии облёта"></label><div id="upload-files"></div><div class="form-group" style="margin-top:20px"><label for="flown-at">Дата и время облёта</label><input class="field" id="flown-at" name="flown_at" type="datetime-local"><span class="subtle">Время Барнаула (UTC+7). Без значения — дата из EXIF на сервере.</span></div><label class="check-label" style="margin-top:16px"><input type="checkbox" name="target_confirmed">Я проверил, что на фотографиях нужное здание</label><div class="form-actions"><a href="#/objects/${esc(id)}" class="btn">Отмена</a><button type="submit" id="upload-submit" class="btn primary" disabled>${icon("upload")}Отправить на обработку</button></div></form><div id="upload-status"></div></div></section>`
  );
}
function renderUploadFiles() {
  const target = document.querySelector("#upload-files");
  if (!target) return;
  target.innerHTML = uploadFiles
    .map(
      (f, i) =>
        `<div class="upload-file"><img src="${esc(f.url)}" alt="Предпросмотр ${esc(f.file.name)}"><div><p>${esc(f.file.name)}</p><small>${(f.file.size / 1024 / 1024).toFixed(1)} МБ</small></div><select data-file-view="${i}" aria-label="Ракурс файла ${esc(f.file.name)}"><option value="">${meta.mock ? "Выберите ракурс" : "Авто (EXIF)"}</option>${meta.views.map((v) => `<option value="${esc(v.id)}" ${f.view === v.id ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>${btn('<span class="sr-only">Удалить файл</span>', "remove-file", "ghost icon-button remove-file", "close", `data-index="${i}"`)}${f.view ? `<div class="upload-gps"><input class="field" type="number" step="any" min="-90" max="90" placeholder="Широта, если нет EXIF" aria-label="Широта ${esc(f.file.name)}" data-gps-lat="${i}" value="${esc(f.lat || "")}"><input class="field" type="number" step="any" min="-180" max="180" placeholder="Долгота, если нет EXIF" aria-label="Долгота ${esc(f.file.name)}" data-gps-lon="${i}" value="${esc(f.lon || "")}"></div>` : ""}</div>`,
    )
    .join("");
  document.querySelector("#upload-submit").disabled = !uploadFiles.length;
}
function addFiles(files) {
  const form = document.querySelector("#upload-form");
  try {
    const incoming = [...files];
    if (incoming.length + uploadFiles.length > 20)
      throw new Error("В одном облёте может быть не более 20 снимков.");
    for (const f of incoming) {
      if (!["image/jpeg", "image/png"].includes(f.type))
        throw new Error(`«${f.name}»: допустимы JPG и PNG.`);
      if (f.size > 30 * 1024 * 1024)
        throw new Error(`«${f.name}» больше 30 МБ.`);
    }
    for (const file of incoming)
      uploadFiles.push({
        file,
        url: URL.createObjectURL(file),
        view: "",
        lat: "",
        lon: "",
      });
    form.querySelector(".form-error")?.remove();
    renderUploadFiles();
  } catch (e) {
    formError(form, e.message);
  }
}
async function pollFlight(fid) {
  const token = routeToken;
  const status = document.querySelector("#upload-status");
  async function tick() {
    if (token !== routeToken) return;
    try {
      const f = await request(`/flights/${fid}`);
      if (f.status === "failed") {
        status.innerHTML = `<div class="form-error" role="alert">${esc(f.error?.message || "Не удалось обработать облёт. Загрузите снимки повторно.")}</div>`;
        document.querySelector("#upload-form")?.classList.remove("hidden");
        return;
      }
      if (f.status === "done") {
        await refresh();
        objectTab = "overview";
        location.hash = `#/objects/${f.object_id}`;
        toast("Облёт обработан. Результат доступен в карточке.");
        return;
      }
      pollTimer = setTimeout(tick, 2000);
    } catch (e) {
      status.innerHTML = `<div class="form-error" role="alert">${esc(e.message)}</div>${btn("Повторить проверку", "retry-flight", "", "refresh", `data-flight="${esc(fid)}"`)}`;
    }
  }
  await tick();
}
async function actPage(fid) {
  const act = await request(`/flights/${encodeURIComponent(fid)}/act`);
  const owner = objects.find((o) => o.last_flight_id === fid);
  return (
    `<a href="${owner ? `#/objects/${esc(owner.id)}` : "#/flights"}" class="back-link">${icon("back")}К результату облёта</a>` +
    heading(
      "Акт осмотра",
      "Проверьте автоматически заполненные данные и дополните поля сотрудника.",
      btn(
        "Печать",
        "print-act",
        "primary",
        "print",
        `data-flight="${esc(fid)}"`,
      ),
    ) +
    `<div class="notice-banner note neutral act-controls">${icon("info")}<div>Изменения в полях акта используются только при печати и не сохраняются после закрытия страницы. ${meta.mock ? "Демонстрационный акт." : "Исходный акт можно открыть отдельно; внесённые здесь изменения в него не попадут."}</div></div><article class="act-sheet" id="act-sheet"><h2 class="act-title">Акт проверки имущества,<br>переданного (передаваемого) в залог</h2><p>Кредитный договор № ${esc(act.loan_id || "_______________")}</p><p>Дата осмотра: ${date(act.inspected_at, { hour: "2-digit", minute: "2-digit" })}</p><div class="form-grid">${(act.fields || []).map((f) => `<div class="form-group ${f.id === "address" ? "full" : ""}"><label for="act-${esc(f.id)}">${esc(f.label)}<span class="field-source">${esc(sources[f.source] || f.source)}</span></label><input id="act-${esc(f.id)}" class="field ${f.source === "employee" && !f.value ? "needs-fill" : ""}" value="${esc(f.value ?? "")}" ${f.source === "employee" ? "" : "readonly"} placeholder="Заполните перед печатью"></div>`).join("")}</div><div class="form-group"><label for="act-defects">При осмотре установлено</label><textarea id="act-defects">${esc((act.defects || []).join("\n"))}</textarea></div><div class="form-grid"><div class="form-group"><label for="act-condition">Состояние имущества</label><select id="act-condition"><option value="satisfactory" ${(act.condition || act.condition_suggested) === "satisfactory" ? "selected" : ""}>Удовлетворительное</option><option value="unsatisfactory" ${(act.condition || act.condition_suggested) === "unsatisfactory" ? "selected" : ""}>Неудовлетворительное</option></select></div><div class="form-group"><label for="act-inspector">Осмотр проводил: ФИО, должность</label><input id="act-inspector" class="field" value="${esc(act.inspector || settings().user)}"></div></div><p class="subtle">Осмотр с применением БПЛА · облёт ${esc(fid)}${meta.mock ? " · Демонстрационные данные" : ""}</p><p style="margin-top:30px">Подпись сотрудника: ___________________</p>${!meta.mock ? `<p class="act-controls" style="margin-top:22px"><a class="text-link" target="_blank" rel="noopener" href="/api/v1/flights/${encodeURIComponent(fid)}/act?format=html">Открыть HTML-акт с сервера ${icon("arrow")}</a></p>` : ""}</article>`
  );
}
function showSettings() {
  const s = settings();
  showDialog(
    "Подключение и профиль",
    `<p>Идентификатор передаётся в заголовке X-User. Имя используется для отображения в интерфейсе.</p><form id="settings-form"><div class="form-grid"><div class="form-group full"><label for="user-name">Имя сотрудника</label><input class="field" id="user-name" name="display_name" value="${esc(s.display_name)}" required maxlength="80"></div><div class="form-group full"><label for="user-id">Идентификатор сотрудника (X-User)</label><input class="field" id="user-id" name="user" value="${esc(s.user)}" required maxlength="80" title="Латинские буквы, цифры и символы _ . @ -"><p class="subtle">Например, ivanov. HTTP-заголовок использует латиницу.</p></div><div class="form-group full"><label for="connection-mode">Источник данных</label><select id="connection-mode" name="mode"><option value="demo" ${s.mode === "demo" ? "selected" : ""}>Демонстрационные данные</option><option value="api" ${s.mode === "api" ? "selected" : ""}>Настоящий API /api/v1</option></select><p class="subtle">Для API запустите локальный сервер с API_TARGET, указывающим адрес бэкенда. При ошибке API демо не включается автоматически.</p></div></div><div class="form-actions">${btn("Закрыть", "close-dialog")}<button class="btn primary" type="submit">Применить</button></div></form>${s.mode === "demo" ? `<details><summary>Восстановить сценарий демонстрации</summary><p class="subtle" style="margin:12px 0">Будут сброшены только локальные демо-объекты, импортированные записи и решения.</p>${btn("Сбросить демо-данные", "reset-demo", "small danger")}</details>` : ""}`,
  );
}
function chooseUpload() {
  showDialog(
    "Выберите объект для облёта",
    `<p>Снимки будут привязаны к выбранному объекту.</p><form id="choose-upload-form"><div class="form-group"><label for="upload-object-id">Объект</label><select id="upload-object-id" name="id" required><option value="">Выберите из реестра</option>${objects.map((o) => `<option value="${esc(o.id)}">${esc(o.address)} · ${esc(o.cadastral)}</option>`).join("")}</select></div><div class="form-actions">${btn("Отмена", "close-dialog")}<button type="submit" class="btn primary">Продолжить</button></div></form>`,
  );
}
function showPhoto(id) {
  const im = window.__photos?.find((x) => x.id === id);
  if (!im) return;
  const detections = (im.detections || [])
    .map((d) => {
      if (
        !Array.isArray(d.bbox) ||
        d.bbox.length !== 4 ||
        d.bbox.some((x) => !Number.isFinite(x))
      )
        return "";
      const [x1, y1, x2, y2] = d.bbox.map((x) => Math.max(0, Math.min(1, x)));
      const classColor = meta.classes.find((c) => c.id === d.class)?.color;
      const c = /^#[\da-f]{3,8}$/i.test(classColor || "")
        ? classColor
        : "#2563eb";
      return `<div class="bbox" style="left:${x1 * 100}%;top:${y1 * 100}%;width:${(x2 - x1) * 100}%;height:${(y2 - y1) * 100}%;--bbox-color:${c}"><span>${esc(d.label || label("classes", d.class))} ${Math.round(d.conf * 100)}%</span></div>`;
    })
    .join("");
  showDialog(
    esc(label("views", im.view)),
    `<div class="row between" style="margin-bottom:20px"><label class="check-label"><input type="checkbox" id="show-boxes" checked>Показать рамки</label><span class="subtle">${im.detections?.length || 0} детекций</span></div><div style="text-align:center"><div class="photo-stage"><img src="${esc(im.url)}" alt="${esc(label("views", im.view))}: оригинал"><div id="photo-boxes">${detections}</div></div></div><p class="subtle" style="margin-top:14px">${im.view_source === "manual" ? "Ракурс задан вручную" : im.view_source === "exif" ? "Ракурс из EXIF" : "Ракурс не указан"}${im.sha256 ? ` · SHA256: ${esc(im.sha256)}` : ""}</p>`,
    { wide: true },
  );
}
async function performReview(action) {
  const f = currentObject?.last_flight;
  if (!f) return;
  await request(`/flights/${f.flight_id}/review`, {
    method: "POST",
    body: { action, comment: "" },
  });
  await refresh();
  await render();
  toast("Результат облёта принят.");
}
function correctionDialog() {
  const f = currentObject.last_flight;
  const value = f.readiness_review || f.review || f.result || {};
  showDialog(
    "Исправить результат облёта",
    `<p>Исходный ответ модели сохранится.</p><form id="review-form"><div class="form-grid"><div class="form-group"><label for="correct-stage">Стадия</label><select id="correct-stage" name="stage" required>${options("stages", value.stage, "Выберите стадию")}</select></div><div class="form-group"><label for="correct-pct">Готовность, %</label><input class="field" id="correct-pct" name="readiness_pct" type="number" min="0" max="100" required value="${value.readiness_pct ?? ""}"></div><div class="form-group"><label for="correct-bank">Шкала банка</label><select id="correct-bank" name="bank_readiness" required>${options("bank_readiness", value.bank_readiness, "Выберите оценку")}</select></div><div class="form-group full"><label for="correct-comment">Причина исправления</label><textarea id="correct-comment" name="comment" required></textarea></div></div><button type="submit" class="btn primary">Сохранить правку</button></form>`,
  );
}

function reasonDialog(title, action, attributes = "") {
  showDialog(
    title,
    `<form id="reason-form" data-reason-action="${action}" ${attributes}><div class="form-group"><label for="reason-comment">Причина / комментарий</label><textarea id="reason-comment" name="comment" required placeholder="Укажите основание решения"></textarea></div><div class="form-actions">${btn("Отмена", "close-dialog")}<button type="submit" class="btn primary">Сохранить решение</button></div></form>`,
  );
}
async function render(preserveDraft = false) {
  const activeForm = preserveDraft
    ? document.querySelector("#step-form, #strategy-form")
    : null;
  const draft = activeForm
    ? {
        id: activeForm.id,
        step: activeForm.dataset.step,
        fields: [...new FormData(activeForm)],
        focus: document.activeElement?.id,
      }
    : null;
  const token = ++routeToken;
  clearTimeout(pollTimer);
  const route = location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .map(decodeURIComponent);
  if (dialog.open) dialog.close();
  if (!meta) return;
  root.className = "shell";
  root.innerHTML = navigation();
  const main = document.querySelector("#main");
  main.innerHTML =
    '<div class="boot"><span class="spinner"></span>Загружаем данные…</div>';
  try {
    let html;
    switch (route[0]) {
      case "objects":
        html = route[1] ? await objectPage(route[1]) : await objectsPage();
        break;
      case "tasks":
        html = supports(meta, "tasks")
          ? tasksPage()
          : empty(
              "Задачи пока недоступны",
              "Проверки фотографий доступны в карточке объекта.",
            );
        break;
      case "flights":
        html = flightsPage();
        break;
      case "new":
        html = newObjectPage();
        break;
      case "import":
        html = supports(meta, "registry_import")
          ? importPage()
          : empty("Импорт пока недоступен", "Добавьте объект через форму.");
        break;
      case "upload":
        html = await uploadPage(route[1]);
        break;
      case "act":
        html = supports(meta, "act")
          ? await actPage(route[1])
          : empty(
              "Акт пока недоступен",
              "Результаты доступны в карточке объекта.",
            );
        break;
      case "overview":
      case "":
        html = overview();
        break;
      default:
        html = empty(
          "Страница не найдена",
          "Откройте обзор или выберите объект в реестре.",
          "search",
        );
    }
    if (token !== routeToken) return;
    main.innerHTML = html + footer();
    document.title = `${main.querySelector("h1")?.textContent || "Мониторинг"} · ИЖС`;
    bindDropzones();
    if (draft) {
      const form = document.getElementById(draft.id);
      if (form && form.dataset.step === draft.step) {
        for (const [name, value] of draft.fields) {
          const field = form.elements.namedItem(name);
          if (field instanceof RadioNodeList) field.value = value;
          else if (field) field.value = value;
        }
        if (draft.focus)
          document.getElementById(draft.focus)?.focus({ preventScroll: true });
      }
    }
  } catch (e) {
    if (token === routeToken)
      main.innerHTML = `<div class="error-page">${icon("alert")}<h1>Не удалось загрузить данные</h1><p>${esc(e.message)}</p><div class="row" style="justify-content:center">${btn("Повторить", "retry", "primary", "refresh")}${btn("Подключение", "settings", "", "settings")}</div></div>`;
  }
}
async function boot() {
  root.innerHTML =
    '<div class="boot"><span class="spinner"></span>Подключаем мониторинг…</div>';
  try {
    meta = normalizeMeta(await request("/meta"));
    await refresh();
    await render();
  } catch (e) {
    root.innerHTML = `<main class="error-page"><h1>Мониторинг пока недоступен</h1><p>${esc(e.message)}</p><div class="row" style="justify-content:center">${btn("Повторить подключение", "boot", "primary", "refresh")}${btn("Подключение и профиль", "settings", "", "settings")}</div></main>`;
  }
}
function bindDropzones() {
  for (const [id, handler] of [
    ["import-drop", (files) => importSelected(files[0])],
    ["flight-drop", addFiles],
  ]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      el.classList.add("drag");
    });
    el.addEventListener("dragleave", () => el.classList.remove("drag"));
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      el.classList.remove("drag");
      handler(e.dataTransfer.files);
    });
  }
}
document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-action]");
  if (!el || el.disabled) return;
  const action = el.dataset.action;
  try {
    if (action === "close-dialog") {
      dialog.close();
      return;
    }
    if (action === "menu") {
      document.querySelector("#sidebar").classList.toggle("open");
      return;
    }
    if (action === "settings") {
      showSettings();
      return;
    }
    if (action === "help") {
      showDialog(
        "Как работает мониторинг",
        `<p>Система помогает заметить отставание строительства до появления просрочки.</p><ol style="padding-left:20px;font-size:13px;line-height:1.9"><li>Импортируйте реестр или добавьте объект.</li><li>Загрузите облёт: фасады, дом и участок сверху.</li><li>Изучите риск, план/факт и доказательства.</li><li>Внесите исход звонка или проверки.</li><li>Подтвердите предложенную стратегию и подготовьте акт.</li></ol><div class="note neutral" style="margin-top:20px">${icon("info")}<div>В демо используются синтетические сценарии. Реальный API подключается в настройках. Риск клиента, стратегия банка и состояние данных показаны отдельно.</div></div>`,
      );
      return;
    }
    if (action === "reset-demo") {
      resetDemo();
      dialog.close();
      await boot();
      toast("Исходные 10 демо-сценариев восстановлены.");
      return;
    }
    if (action === "boot") {
      await boot();
      return;
    }
    if (action === "retry") {
      await refresh();
      await render();
      return;
    }
    if (action === "go-import") {
      location.hash = "#/import";
      return;
    }
    if (action === "go-new") {
      location.hash = "#/new";
      return;
    }
    if (action === "choose-upload") {
      chooseUpload();
      return;
    }
    if (action === "upload-object") {
      location.hash = `#/upload/${currentObject.id}`;
      return;
    }
    if (action === "risk-filter") {
      filters = { ...filters, risk_level: el.dataset.risk, page: 1 };
      if (location.hash === "#/objects") await render();
      else location.hash = "#/objects";
      return;
    }
    if (action === "strategy-filter") {
      filters = {
        ...filters,
        risk_level: "",
        strategy: el.dataset.strategy,
        page: 1,
      };
      location.hash = "#/objects";
      return;
    }
    if (action === "alert-tab") {
      alertFilter = el.dataset.tab;
      await render();
      document
        .querySelector(`[data-action="alert-tab"][data-tab="${alertFilter}"]`)
        ?.focus({ preventScroll: true });
      return;
    }
    if (action === "object-tab") {
      objectTab = el.dataset.tab;
      await render();
      document
        .querySelector(`[data-action="object-tab"][data-tab="${objectTab}"]`)
        ?.focus({ preventScroll: true });
      return;
    }
    if (action === "reset-filters") {
      filters = {
        risk_level: "",
        strategy: "",
        stage: "",
        data_status: "",
        overdue: "",
        pledged: "",
        q: "",
        sort: "-risk",
        page: 1,
      };
      await render();
      return;
    }
    if (action === "sort") {
      filters.sort = el.dataset.sort;
      filters.page = 1;
      await render();
      return;
    }
    if (action === "page-prev" || action === "page-next") {
      filters.page += action === "page-prev" ? -1 : 1;
      await render();
      return;
    }
    if (action === "accept-alert") {
      el.disabled = true;
      const a = alerts.find((x) => x.id === el.dataset.id);
      await request(`/alerts/${el.dataset.id}/actions`, {
        method: "POST",
        body: {
          decision: "confirm",
          action_code: a.recommended_action?.code,
          outcome: "Мера принята сотрудником",
          close_alert: false,
        },
      });
      await refresh();
      objectTab = "overview";
      location.hash = `#/objects/${el.dataset.object}`;
      toast("Мера принята. Выполните следующий шаг в карточке.");
      return;
    }
    if (action === "reject-alert") {
      reasonDialog(
        "Отметить ложную тревогу",
        "reject-alert",
        `data-id="${esc(el.dataset.id)}"`,
      );
      return;
    }
    if (action === "material-confirm" || action === "material-reject") {
      if (currentObject.conductor?.next_step?.code === "MANUAL_INSPECTION") {
        reasonDialog(
          action === "material-confirm"
            ? "Подтвердить расхождение материала"
            : "Отклонить расхождение материала",
          "material",
          `data-outcome="${action === "material-confirm" ? "confirmed" : "rejected"}"`,
        );
      } else
        toast("Дирижёр сейчас не ожидает ручную проверку. Обновите карточку.");
      return;
    }
    if (action === "analysis-request") {
      el.disabled = true;
      await request(`/objects/${currentObject.id}/analysis`, {
        method: "POST",
        body: {},
      });
      await render();
      return;
    }
    if (action === "material-accept") {
      el.disabled = true;
      await request(
        `/flights/${currentObject.last_flight.flight_id}/material/review`,
        { method: "POST", body: { action: "accept" } },
      );
      await refresh();
      await render();
      toast("Материал принят.");
      return;
    }
    if (action === "material-correct") {
      materialCorrectionDialog();
      return;
    }
    if (action === "review-accept") {
      el.disabled = true;
      await performReview("accept");
      return;
    }
    if (action === "review-correct") {
      correctionDialog();
      return;
    }
    if (action === "reshoot") {
      reasonDialog("Отправить на пересъёмку", "reshoot");
      return;
    }
    if (action === "remove-file") {
      const index = Number(el.dataset.index);
      URL.revokeObjectURL(uploadFiles[index].url);
      uploadFiles.splice(index, 1);
      renderUploadFiles();
      return;
    }
    if (action === "retry-flight") {
      await pollFlight(el.dataset.flight);
      return;
    }
    if (action === "view-photo") {
      showPhoto(el.dataset.id);
      return;
    }
    if (action === "print-act") {
      window.print();
      return;
    }
    if (action === "add-checkpoint") {
      const id = Date.now();
      document
        .querySelector("#checkpoint-rows")
        .insertAdjacentHTML(
          "beforeend",
          `<div class="row checkpoint-row" style="margin-bottom:10px"><select aria-label="Стадия контрольной точки" data-checkpoint-stage required>${meta.stages.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("")}</select><input class="field" type="date" aria-label="Дата контрольной точки" data-checkpoint-date required>${btn('<span class="sr-only">Удалить контрольную точку</span>', "remove-checkpoint", "ghost icon-button", "close")}</div>`,
        );
      return;
    }
    if (action === "remove-checkpoint") {
      el.closest(".checkpoint-row").remove();
      return;
    }
  } catch (err) {
    toast(err.message);
    el.disabled = false;
  }
});
document.addEventListener("submit", async (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  e.preventDefault();
  const decision = e.submitter?.value;
  const fd = new FormData(form);
  if (form.id === "global-search") {
    filters.q = String(fd.get("q") || "");
    filters.page = 1;
    if (location.hash === "#/objects") await render();
    else location.hash = "#/objects";
    return;
  }
  if (form.id === "filters") {
    for (const [k, v] of fd) filters[k] = v;
    filters.page = 1;
    await render();
    return;
  }
  if (form.id === "choose-upload-form") {
    dialog.close();
    location.hash = `#/upload/${encodeURIComponent(fd.get("id"))}`;
    return;
  }
  await submit(form, async () => {
    if (form.id === "settings-form") {
      const prior = settings();
      const next = {
        user: String(fd.get("user")).trim(),
        display_name: String(fd.get("display_name")).trim(),
        mode: fd.get("mode"),
      };
      if (!next.user || !next.display_name)
        throw new Error("Укажите имя и идентификатор сотрудника.");
      if (!/^[a-zA-Z0-9_.@-]+$/.test(next.user))
        throw new Error(
          "В идентификаторе используйте латинские буквы, цифры и символы _ . @ -",
        );
      saveSettings(next);
      try {
        const newMeta = normalizeMeta(await request("/meta"));
        meta = newMeta;
        await refresh();
      } catch (e) {
        saveSettings(prior);
        throw e;
      }
      dialog.close();
      await boot();
      toast("Настройки применены.");
    }
    if (form.id === "step-form") {
      await request(`/objects/${currentObject.id}/steps`, {
        method: "POST",
        body: {
          step: form.dataset.step,
          outcome: fd.get("outcome"),
          comment: String(fd.get("comment") || "").trim(),
        },
      });
      await refresh();
      await render();
      toast("Исход сохранён. Следующий шаг обновлён.");
    }
    if (form.id === "strategy-form") {
      const comment = String(fd.get("comment") || "").trim();
      if (decision === "reject" && !comment)
        throw new Error("Укажите причину отклонения в комментарии.");
      await request(`/objects/${currentObject.id}/strategy`, {
        method: "POST",
        body: {
          decision,
          strategy: currentObject.conductor.strategy.code,
          comment,
        },
      });
      await refresh();
      await render();
      toast(
        decision === "confirm"
          ? "Стратегия подтверждена."
          : "Стратегия отклонена.",
      );
    }
    if (form.id === "reason-form") {
      const comment = String(fd.get("comment") || "").trim();
      if (!comment) throw new Error("Укажите причину решения.");
      if (form.dataset.reasonAction === "reject-alert")
        await request(`/alerts/${form.dataset.id}/actions`, {
          method: "POST",
          body: { decision: "reject", outcome: comment, close_alert: true },
        });
      else if (form.dataset.reasonAction === "reshoot")
        await request(
          `/flights/${currentObject.last_flight.flight_id}/reshoot`,
          { method: "POST", body: { comment } },
        );
      else if (form.dataset.reasonAction === "material")
        await request(`/objects/${currentObject.id}/steps`, {
          method: "POST",
          body: {
            step: "MANUAL_INSPECTION",
            outcome: form.dataset.outcome,
            comment,
          },
        });
      dialog.close();
      await refresh();
      await render();
      toast("Решение сохранено.");
    }
    if (form.id === "material-review-form") {
      await request(
        `/flights/${currentObject.last_flight.flight_id}/material/review`,
        {
          method: "POST",
          body: {
            action: "correct",
            material: fd.get("material"),
            comment: String(fd.get("comment") || "").trim(),
          },
        },
      );
      dialog.close();
      await refresh();
      await render();
      toast("Материал сохранён.");
    }
    if (form.id === "review-form") {
      await request(`/flights/${currentObject.last_flight.flight_id}/review`, {
        method: "POST",
        body: {
          action: "correct",
          stage: fd.get("stage"),
          readiness_pct: Number(fd.get("readiness_pct")),
          bank_readiness: Number(fd.get("bank_readiness")),
          comment: String(fd.get("comment")).trim(),
        },
      });
      dialog.close();
      await refresh();
      await render();
      toast("Правка сохранена отдельно от результата модели.");
    }
    if (form.id === "valuation-form") {
      const body = {};
      for (const k of ["area_m2", "unit_cost_rub_m2", "land_rub"])
        body[k] = fd.get(k) === "" ? null : Number(fd.get(k));
      body.wall_material = fd.get("wall_material") || null;
      body.rate_note = String(fd.get("rate_note") || "").trim();
      await request(`/objects/${currentObject.id}/valuation`, {
        method: "POST",
        body,
      });
      await render();
      toast("Оценка пересчитана.");
    }
    if (form.id === "new-object") {
      const body = {};
      for (const k of [
        "address",
        "cadastral",
        "loan_id",
        "started_at",
        "planned_completion_at",
        "actual_completion_at",
      ])
        body[k] = String(fd.get(k) || "").trim() || null;
      for (const k of ["overdue", "registered", "pledged", "has_aerial_photo"])
        body[k] = fd.has(k);
      if (body.planned_completion_at < body.started_at)
        throw new Error("Плановая дата раньше даты начала строительства.");
      if (
        fd.get("wall_material") ||
        fd.get("floors") ||
        fd.get("footprint_m2") ||
        fd.get("total_area_m2")
      ) {
        body.project = {};
        for (const k of ["floors", "footprint_m2", "total_area_m2"])
          if (fd.get(k)) body.project[k] = Number(fd.get(k));
        if (fd.get("wall_material"))
          body.project.wall_material = fd.get("wall_material");
        if (fd.get("project_source"))
          body.project.source = fd.get("project_source");
      }
      body.checkpoints = [...form.querySelectorAll(".checkpoint-row")].map(
        (row) => ({
          stage: row.querySelector("[data-checkpoint-stage]").value,
          due: row.querySelector("[data-checkpoint-date]").value,
        }),
      );
      if (
        body.checkpoints.some(
          (c) => c.due < body.started_at || c.due > body.planned_completion_at,
        )
      )
        throw new Error(
          "Контрольные точки должны попадать в срок строительства.",
        );
      const created = await request("/objects", { method: "POST", body });
      await refresh();
      objectTab = "overview";
      location.hash = `#/objects/${encodeURIComponent(created.id)}`;
      toast("Объект добавлен.");
    }
    if (form.id === "import-form") {
      if (!importFile) throw new Error("Выберите файл реестра.");
      const result = await importRegistry(importFile, importRows);
      await refresh();
      form.classList.add("hidden");
      document.querySelector("#import-result").innerHTML =
        `<div class="import-counts"><div><strong>${result.created}</strong><span>Создано</span></div><div><strong>${result.updated}</strong><span>Обновлено</span></div><div><strong>${result.errors?.length || 0}</strong><span>Строк с ошибками</span></div></div>${result.errors?.length ? `<div class="table-wrap"><table><thead><tr><th>Строка</th><th>Ошибка</th></tr></thead><tbody>${result.errors.map((x) => `<tr><td>${x.row}</td><td>${esc(x.message)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="note">' + icon("check") + "<div>Реестр импортирован без ошибок.</div></div>"}<div class="form-actions"><a class="btn primary" href="#/objects">К списку объектов</a></div>`;
    }
    if (form.id === "upload-form") {
      if (!uploadFiles.length) throw new Error("Добавьте снимки.");
      if (settings().mode === "demo" && uploadFiles.some((f) => !f.view))
        throw new Error("Укажите ракурс для каждого файла.");
      const body = new FormData();
      body.append("target_confirmed", String(fd.has("target_confirmed")));
      for (const f of uploadFiles) body.append("files", f.file);
      body.append(
        "views",
        JSON.stringify(uploadFiles.map((f) => f.view || null)),
      );
      body.append(
        "meta",
        JSON.stringify(
          uploadFiles.map((f) =>
            f.lat !== "" && f.lon !== ""
              ? { gps: [Number(f.lat), Number(f.lon)] }
              : {},
          ),
        ),
      );
      if (fd.get("flown_at"))
        body.append("flown_at", `${fd.get("flown_at")}:00+07:00`);
      try {
        const res = await request(`/objects/${currentObject.id}/flights`, {
          method: "POST",
          body,
        });
        form.classList.add("hidden");
        document.querySelector("#upload-status").innerHTML =
          `<div class="processing"><span class="spinner"></span><div><h3>Обрабатываем облёт</h3><p>Определяем стадию, готовность и сигналы риска. Можно оставить эту страницу открытой.</p></div></div>`;
        await pollFlight(res.flight_id);
      } catch (e) {
        if (e.code === "VIEWS_REQUIRED") {
          toast("В снимках нет EXIF. Укажите ракурсы и координаты.");
        }
        throw e;
      }
    }
  });
});
document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.id === "import-file") importSelected(el.files[0]);
  if (el.id === "flight-files") {
    addFiles(el.files);
    el.value = "";
  }
  if (el.matches("[data-file-view]")) {
    uploadFiles[Number(el.dataset.fileView)].view = el.value;
    renderUploadFiles();
  }
  if (el.matches("[data-gps-lat]"))
    uploadFiles[Number(el.dataset.gpsLat)].lat = el.value;
  if (el.matches("[data-gps-lon]"))
    uploadFiles[Number(el.dataset.gpsLon)].lon = el.value;
  if (el.id === "show-boxes")
    document
      .querySelector("#photo-boxes")
      .classList.toggle("hidden", !el.checked);
  if (el.id === "task-risk")
    document.querySelector("#task-rows").innerHTML = taskRows(
      tasks.filter((t) => !el.value || t.risk_level === el.value),
    );
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape")
    document.querySelector("#sidebar")?.classList.remove("open");
  if (
    ["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key) &&
    e.target.matches('[role="tab"]')
  ) {
    const tabs = [
        ...e.target
          .closest('[role="tablist"]')
          .querySelectorAll('[role="tab"]'),
      ],
      index = tabs.indexOf(e.target),
      next =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? tabs.length - 1
            : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
    e.preventDefault();
    tabs[next].click();
  }
});
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      dialog.close();
  }
});
window.addEventListener("hashchange", () => {
  objectTab = "overview";
  window.scrollTo(0, 0);
  render();
});
window.addEventListener("beforeunload", () =>
  uploadFiles.forEach((f) => URL.revokeObjectURL(f.url)),
);
await boot();
