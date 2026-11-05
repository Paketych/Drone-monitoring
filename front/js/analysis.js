const FACT_LABELS = {
  "inspection.processing": "Обработка фотографий",
  "inspection.has_flight": "Наличие облёта",
  "inspection.stale": "Нужны свежие снимки",
  "inspection.future_dated": "Дата съёмки находится в будущем",
  "readiness.trusted": "Готовность подтверждена",
  "material.match": "Сравнение материала с проектом",
  "plan.basis": "Основа графика",
  "plan.lag_suspected": "Признак отставания",
  "term.expired": "Плановый срок истёк",
  "readiness.level": "Шкала готовности",
  "budget.status": "Доступность расчёта стоимости",
  "budget.completion_scope": "Комплект работ в расчёте",
  "budget.method": "Метод расчёта",
  "budget.is_demo": "Демонстрационные тарифы",
  "budget.full_cost_rub": "Готовый контур, ₽",
  "budget.completed_cost_rub": "Условная выполненная часть, ₽",
  "budget.remaining_cost_rub": "Остаток на достройку, ₽",
};

export function analysisPanel(report, { esc, btn }) {
  const pending = report.llm_status === "pending";
  const title = report.stale
    ? "Данные изменились — обновите сводку"
    : {
        accepted: "AI · предложение проверено",
        pending: "AI готовит объяснение",
        unavailable: "Заключение по правилам",
        rejected: "Ответ AI отклонён · заключение по правилам",
      }[report.llm_status] || "Заключение по правилам";
  const connection = report.connection || {};
  const note = connection.mock
    ? "Запросы к AI отключены в деморежиме сервера."
    : !connection.enabled
      ? "Подключение к AI выключено."
      : !connection.configured
        ? "Ключ доступа к AI ещё не задан на сервере."
        : "";
  return `<section class="panel section-block" id="ai-analysis"><div class="panel-head"><h2>AI-сводка инспекции</h2><span>${esc(title)}</span></div><div class="panel-body"><p>${esc(report.explanation)}</p>${report.next_step ? `<p style="margin-top:12px"><strong>Следующий шаг:</strong> ${esc(report.next_step.label)}</p>` : ""}<p class="subtle" style="margin-top:12px">Это предложение для сотрудника. Действия не выполняются автоматически.</p>${note ? `<p class="subtle">${esc(note)}</p>` : ""}${report.error && !note ? `<p class="subtle">${esc(report.error.message)}</p>` : ""}<details><summary>Факты, на которых основан вывод</summary><dl class="facts">${(report.evidence_refs || []).map((ref) => `<dt>${esc(FACT_LABELS[ref] || ref)}</dt><dd>${esc(report.facts?.[ref] === null ? "Неизвестно" : report.facts?.[ref] === true ? "Да" : report.facts?.[ref] === false ? "Нет" : String(report.facts?.[ref]))}</dd>`).join("")}</dl></details><div class="action-buttons" style="margin-top:16px">${btn(pending ? "Готовится заключение…" : "Обновить AI-сводку", "analysis-request", "small", "", pending ? "disabled" : "")}</div></div></section>`;
}
