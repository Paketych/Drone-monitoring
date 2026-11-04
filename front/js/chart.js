export function progressChart(history, escape) {
  const plan = history.plan || [],
    fact = history.fact || [];
  if (!plan.length)
    return '<div class="empty"><p>Нет данных для графика.</p></div>';
  const w = 600,
    h = 245,
    left = 43,
    right = 30,
    top = 20,
    bottom = 42;
  const dates = [
    ...plan.map((x) => Date.parse(x.date)),
    ...fact.map((x) => Date.parse(x.flown_at)),
  ].filter(Number.isFinite);
  if (history.forecast?.completion_at)
    dates.push(Date.parse(history.forecast.completion_at));
  const min = Math.min(...dates),
    max = Math.max(...dates),
    x = (d) =>
      left + ((Date.parse(d) - min) / (max - min || 1)) * (w - left - right),
    y = (p) =>
      h - bottom - (Math.max(0, Math.min(100, p)) / 100) * (h - top - bottom);
  const path = (pts) =>
    pts
      .map(
        (p, i) =>
          `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.pct).toFixed(1)}`,
      )
      .join(" ");
  let svg = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="chart-title chart-description"><title id="chart-title">План и факт строительства</title><desc id="chart-description">${escape(`План от 0 до 100 процентов. ${fact.map((f) => `Облёт ${f.flown_at.slice(0, 10)}: ${f.readiness_pct} процентов.`).join(" ")}`)}</desc>`;
  for (const tick of [0, 25, 50, 75, 100])
    svg += `<line x1="${left}" y1="${y(tick)}" x2="${w - right}" y2="${y(tick)}" stroke="#e8eee9" ${tick ? 'stroke-dasharray="3 5"' : ""}/><text x="${left - 12}" y="${y(tick) + 4}" text-anchor="end">${tick}%</text>`;
  const end = plan.at(-1);
  svg += `<line x1="${x(end.date)}" y1="${top}" x2="${x(end.date)}" y2="${h - bottom}" stroke="#c3cfc6" stroke-dasharray="4 4"/><path d="${path(plan)}" fill="none" stroke="#a9b7ad" stroke-width="2"/>`;
  const facts = [
    { date: plan[0].date, pct: 0 },
    ...fact.map((f) => ({ date: f.flown_at, pct: f.readiness_pct })),
  ];
  if (fact.length)
    svg += `<path d="${path(facts)}" fill="none" stroke="#2563eb" stroke-width="2.5"/>`;
  for (const f of fact)
    svg += `<circle cx="${x(f.flown_at)}" cy="${y(f.readiness_pct)}" r="4.5" fill="#2563eb" stroke="white" stroke-width="2"><title>${escape(f.flown_at.slice(0, 10))}: ${f.readiness_pct}%${f.reviewed ? " · исправлено сотрудником" : ""}</title></circle><text class="chart-label" x="${Math.min(w - 55, x(f.flown_at) + 10)}" y="${y(f.readiness_pct) + 4}">${f.readiness_pct}%</text>`;
  if (history.forecast?.completion_at && fact.length) {
    const last = fact.at(-1);
    svg += `<path d="${path([
      { date: last.flown_at, pct: last.readiness_pct },
      { date: history.forecast.completion_at, pct: 100 },
    ])}" fill="none" stroke="#af7937" stroke-width="2" stroke-dasharray="5 5"/>`;
  }
  const fmt = (d) =>
    new Date(d).toLocaleDateString("ru-RU", {
      month: "short",
      year: "2-digit",
      timeZone: "Asia/Barnaul",
    });
  for (let i = 0; i < 5; i++) {
    const d = new Date(min + ((max - min) * i) / 4).toISOString();
    svg += `<text class="axis-label" x="${x(d)}" y="${h - 17}" text-anchor="middle">${escape(fmt(d))}</text>`;
  }
  for (const s of history.steps || []) {
    if (!s.date || Date.parse(s.date) < min || Date.parse(s.date) > max)
      continue;
    svg += `<path d="M${x(s.date) - 3},${h - bottom + 3}l3,5 3,-5" fill="#af7937"><title>${escape(s.label || s.code || "Шаг")}: ${escape(s.comment || "")}</title></path>`;
  }
  return svg + "</svg>";
}
