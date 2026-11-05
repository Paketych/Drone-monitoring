import { percent } from "./capabilities.js";

export function materialConfidenceText(observation) {
  const separate =
    observation.cls != null || observation.detector_confidence != null;
  const notClassified = separate && observation.material_conf === null;
  const material = notClassified
    ? null
    : (observation.material_conf ??
      observation.confidence ??
      (separate ? null : observation.conf));
  const detector =
    observation.detector_confidence ??
    (observation.cls != null ? observation.conf : null);
  return `Материал: ${percent(material)}${detector == null ? "" : ` · Стена: ${percent(detector)}`}`;
}

const readinessStates = {
  determined: "Готовность определена",
  needs_review: "Нужна проверка",
  needs_reshoot: "Нужна пересъёмка",
  model_unavailable: "Модель готовности недоступна",
};

export function inspectionOverview(objects, ui) {
  const { heading, btn, objectLink, esc, dataBadge } = ui;
  return (
    heading(
      "Обзор мониторинга",
      "Фотографии, материалы стен и готовность строительства.",
      btn("Добавить объект", "go-new", "primary", "plus"),
    ) +
    `<section class="panel"><div class="panel-head"><h2>Объекты: ${objects.length}</h2></div><div class="panel-body"><p class="subtle">Оценка банковского риска и стратегии пока недоступны.</p></div><div class="table-wrap"><table><thead><tr><th>Объект</th><th>Готовность</th><th>Данные</th></tr></thead><tbody>${objects.map((o) => `<tr><td>${objectLink(o.id, esc(o.address))}</td><td>${o.readiness_pct ?? "—"}%</td><td>${dataBadge(o.data_status)}</td></tr>`).join("")}</tbody></table></div></section>`
  );
}

export function readinessDetail(o, ui) {
  const { esc, btn, label, date } = ui;
  const f = o.last_flight;
  if (!f)
    return `<section class="panel"><div class="panel-body"><h2>Облётов пока нет</h2><p>Загрузите фотографии этого дома.</p></div></section>`;
  const raw = f.result;
  const review = f.readiness_review || f.review;
  const current = review || raw;
  const ready = f.status === "done";
  const state = review
    ? "Проверено сотрудником"
    : readinessStates[raw?.status] ||
      {
        queued: "В очереди",
        processing: "Обрабатывается",
        failed: "Ошибка обработки",
      }[f.status] ||
      "Результата пока нет";
  return `<section class="panel"><div class="panel-head"><h2>Готовность строительства</h2><span>${esc(state)}</span></div><div class="panel-body"><dl class="facts"><dt>Стадия</dt><dd>${current?.stage ? esc(label("stages", current.stage)) : "Не определена"}</dd><dt>Готовность</dt><dd>${current?.readiness_pct ?? "—"}%</dd><dt>Шкала банка</dt><dd>${current?.bank_readiness ?? "—"} / 100</dd><dt>Дата съёмки</dt><dd>${date(f.flown_at)}</dd><dt>Уверенность модели</dt><dd>${raw?.is_demo ? "Модель недоступна" : percent(raw?.confidence)}</dd></dl><p style="margin-top:16px">${esc(raw?.explanation || f.error?.message || "Ожидается обработка фотографий.")}</p>${review ? `<div class="note neutral" style="margin-top:16px"><div>Проверил ${esc(review.author)}: ${esc(review.comment)}<details><summary>Исходный ответ модели</summary>${raw?.stage ? esc(label("stages", raw.stage)) : "Стадия не определена"} · ${raw?.readiness_pct ?? "—"}%</details></div></div>` : ""}${(raw?.reshoot_reasons || []).length ? `<p class="subtle">Причины пересъёмки: ${raw.reshoot_reasons.map((code) => esc(label("reshoot_reasons", code))).join("; ")}</p>` : ""}<div class="action-buttons" style="margin-top:20px">${ready && raw?.stage && !raw.is_demo ? btn("Согласен", "review-accept", "primary small", "check") : ""}${ready ? btn("Исправить", "review-correct", "small") : ""}</div></div></section>`;
}

export function materialPanel(o, meta, ui) {
  const { esc, btn, label } = ui;
  const f = o.last_flight;
  const result = f?.material_result;
  const fixed = o.material;
  const state = result
    ? label("material_statuses", result.status)
    : "Обработка ещё не выполнена";
  const project = o.project?.wall_material || fixed?.project;
  const crops = (result?.per_view || [])
    .filter((v) => v.crop_url)
    .map(
      (v) =>
        `<div><a href="${esc(v.crop_url)}" target="_blank" rel="noopener"><img style="width:100%" src="${esc(v.crop_url)}" alt="Фрагмент стены"></a><p>${esc(v.view ? label("views", v.view) : "Ракурс не указан")} · ${esc(label("materials", v.class || v.material))}</p><p class="subtle">${materialConfidenceText(v)}</p></div>`,
    )
    .join("");
  return `<section class="panel"><div class="panel-head"><h2>Материал стен</h2><span>${esc(state)}</span></div><div class="panel-body"><dl class="facts"><dt>По проекту</dt><dd>${project ? esc(label("materials", project)) : "Не задан"}</dd><dt>Закреплённый материал</dt><dd>${fixed ? esc(label("materials", fixed.observed)) : "Пока не закреплён"}</dd><dt>Последнее распознавание</dt><dd>${result ? esc(result.material_label) : "Нет результата"}</dd><dt>Уверенность</dt><dd>${result?.is_demo ? "Модель недоступна" : percent(result?.confidence)}</dd></dl>${fixed ? `<p style="margin-top:12px">${fixed.reviewed ? "Материал проверен сотрудником" : "Материал закреплён автоматически"}. ${fixed.match === "mismatch" ? "Материал отличается от проекта." : fixed.match === "match" ? "Материал совпадает с проектом." : ""}</p>` : ""}<p style="margin-top:16px">${esc(result?.explanation || "Загрузите снимки открытых стен.")}</p>${result?.review_reasons?.length ? `<p class="subtle">${result.review_reasons.map((code) => esc(label("material_review_reasons", code))).join("; ")}</p>` : ""}${crops ? `<div class="gallery" style="margin-top:20px">${crops}</div>` : ""}<div class="action-buttons" style="margin-top:20px">${f?.status === "done" && result && !result.is_demo && !["hidden", "unclear"].includes(result.material) ? btn("Принять материал", "material-accept", "primary small") : ""}${f?.status === "done" && meta.capabilities.material_review ? btn("Указать материал вручную", "material-correct", "small") : ""}</div></div></section>`;
}
