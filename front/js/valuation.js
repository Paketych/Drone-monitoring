const AREA_SOURCES = {
  manual: "Введена сотрудником",
  project_total: "Общая площадь из проекта",
  project_footprint_times_floors: "Пятно застройки × этажность (приближение)",
};
const MATERIAL_SOURCES = {
  manual: "выбран для расчёта",
  fixed: "закреплён на объекте",
  project: "из проекта",
};
const READINESS_NOTES = {
  no_flight:
    "Облёта ещё нет. Полную стоимость можно рассчитать сейчас; выполненную часть и остаток — после проверки готовности.",
  processing:
    "Снимки ещё обрабатываются. Выполненная часть и остаток появятся после проверки готовности.",
  failed:
    "Обработка снимков завершилась ошибкой. Загрузите новый облёт для проверки готовности.",
  model_unavailable:
    "Автоматическая готовность недоступна. Сотрудник может проверить снимки и сохранить готовность вручную.",
  target_not_confirmed:
    "Не подтверждено, что на снимках нужный дом. Проверьте объект и готовность перед расчётом выполненных работ.",
  needs_review:
    "Готовность требует проверки. Сохраните результат проверки, чтобы рассчитать выполненную часть и остаток.",
};
const MISSING = {
  area_m2: "Укажите общую площадь дома в форме расчёта.",
  material_or_unit_cost:
    "Выберите материал для расчёта или укажите свою ставку за м².",
  "valuation.configuration":
    "Тарифы недоступны. Нужно проверить конфигурацию на сервере.",
};

export function constructionCostPanel(o, { esc, money, label }) {
  const v = o.valuation || {},
    b = v.breakdown || {},
    inputs = o.valuation_inputs || {};
  const amount = (value) => (value == null ? "Не рассчитано" : money(value));
  const range = (values) =>
    values
      ? `${money(values[0])} — ${money(values[1])}`
      : "Недостаточно данных";
  const cards = [
    ["Готовый контур дома", "full_cost_rub", "full_range_rub"],
    ["Условная выполненная часть", "completed_cost_rub", "completed_range_rub"],
    ["Остаток на достройку", "remaining_cost_rub", "remaining_range_rub"],
  ];
  const missing = v.missing_data || [];
  const materialOptions = Object.entries(v.material_rates || {})
    .map(
      ([material, rate]) =>
        `<option value="${esc(material)}" ${inputs.wall_material === material ? "selected" : ""}>${esc(label("materials", material))} · ${money(rate)}/м²</option>`,
    )
    .join("");
  const reviewAction = missing.includes("confirmed_readiness")
    ? o.last_flight?.status === "done"
      ? '<button type="button" class="btn small" data-action="review-correct">Проверить готовность</button>'
      : o.id && !["queued", "processing"].includes(o.last_flight?.status)
        ? `<a class="btn small" href="#/upload/${encodeURIComponent(o.id)}">Загрузить фотографии</a>`
        : ""
    : "";
  return `<section class="panel section-block" id="construction-cost"><div class="panel-head"><h2>Стоимость постройки</h2><span>${v.is_demo ? "Демонстрационные тарифы" : "Предварительный расчёт"}</span></div><div class="panel-body">
    <p>${esc(v.completion_scope || "Наружный контур дома")}</p>
    <p class="subtle" style="margin-top:8px">Для полной стоимости достаточно площади и материала или своей ставки. Готовность нужна для выполненной части и остатка.</p>
    <form id="valuation-form" style="margin-top:20px"><div class="form-grid">
      <div class="form-group"><label for="cost-area">Общая площадь для расчёта, м²</label><input class="field" id="cost-area" name="area_m2" type="number" min="0.001" max="1000000" step="0.001" value="${esc(inputs.area_m2 ?? "")}" placeholder="${b.area_m2 != null ? esc(b.area_m2) + " — из данных объекта" : "Например, 120"}"><span class="subtle">Общая площадь всех этажей. Пусто — использовать площадь из проекта.</span></div>
      <div class="form-group"><label for="cost-material">Материал для расчёта</label><select class="field" id="cost-material" name="wall_material"><option value="" ${inputs.wall_material == null ? "selected" : ""}>Автоматически: закреплённый материал или проект</option>${materialOptions}</select><span class="subtle">Выбор меняет только расчёт стоимости и не подтверждает материал по снимкам.</span></div>
      <div class="form-group"><label for="cost-rate">Своя ставка, ₽/м²</label><input class="field" id="cost-rate" name="unit_cost_rub_m2" type="number" min="0.01" max="10000000" step="0.01" value="${esc(inputs.unit_cost_rub_m2 ?? "")}" placeholder="Пусто — тариф выбранного материала"><span class="subtle">Заполненная ставка имеет приоритет над тарифом материала.</span></div>
      <div class="form-group"><label for="cost-rate-note">Источник своей ставки</label><input class="field" id="cost-rate-note" name="rate_note" maxlength="1000" value="${esc(inputs.rate_note || "")}" placeholder="Например, смета подрядчика"></div>
      <div class="form-group"><label for="cost-land">Стоимость участка отдельно, ₽</label><input class="field" id="cost-land" name="land_rub" type="number" min="0" max="1000000000000" step="0.01" value="${esc(inputs.land_rub ?? "")}"></div>
    </div><p class="subtle">Пустое поле снимает ручную настройку.</p><div class="form-actions"><button type="submit" class="btn primary">Сохранить параметры и пересчитать</button></div></form>
    ${missing
      .filter((key) => key !== "confirmed_readiness")
      .map(
        (key) =>
          `<p class="note warning" style="margin-top:12px">${esc(MISSING[key] || key)}</p>`,
      )
      .join("")}
    <div class="form-grid" style="margin-top:24px">${cards.map(([title, key, rangeKey]) => `<article><h3>${title}</h3><p class="value-amount" data-cost="${key}">${amount(v[key])}</p><p class="subtle">${range(v[rangeKey])}</p></article>`).join("")}</div>
    <p style="margin-top:20px">${esc(v.explanation || "Укажите площадь и материал или ставку, затем нажмите «Пересчитать».")}</p>
    ${missing.includes("confirmed_readiness") ? `<div class="note warning" style="margin-top:12px"><div>${esc(READINESS_NOTES[b.readiness_status] || READINESS_NOTES.needs_review)}${reviewAction ? `<div class="action-buttons" style="margin-top:12px">${reviewAction}</div>` : ""}</div></div>` : ""}
    ${v.range_percent != null ? `<p class="subtle">Вилка ±${esc(v.range_percent)}% — условный сценарий для демонстрации, не статистическая точность.</p>` : ""}
    <dl class="facts" style="margin-top:20px"><dt>Общая площадь</dt><dd>${b.area_m2 == null ? "Не указана" : `${esc(b.area_m2)} м² · ${esc(AREA_SOURCES[b.area_source] || "")}`}</dd><dt>Материал</dt><dd>${b.material ? `${esc(label("materials", b.material))} · ${esc(MATERIAL_SOURCES[b.material_source] || "")}` : "Не определён"}</dd><dt>Ставка</dt><dd>${b.unit_cost_rub_m2 == null ? "Не указана" : `${money(b.unit_cost_rub_m2)} / м² · ${b.rate_source === "manual" ? "ввод сотрудника" : "конфигурация тарифов"}`}</dd><dt>Готовность</dt><dd>${b.readiness_pct == null ? "Не подтверждена" : `${esc(b.readiness_pct)}% · ${b.readiness_source === "employee" ? "проверка сотрудника" : b.readiness_source === "demo" ? "демонстрационный сценарий" : "результат модели"}`}</dd><dt>Участок отдельно</dt><dd>${b.land_rub == null ? "Стоимость не указана" : money(b.land_rub)}</dd></dl>
    <p class="subtle" style="margin-top:16px">Не входит в стоимость контура: ${esc(v.excludes || "земля, отделка и инженерные сети")}. Участок не включён в три суммы выше.</p>
    ${(v.warnings || []).map((text) => `<p class="note warning" style="margin-top:12px">${esc(text)}</p>`).join("")}
  </div></section>`;
}
