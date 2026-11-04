import rules from "./valuation-demo-config.js";

function invalid(message) {
  const error = new Error(message);
  error.code = "VALIDATION_ERROR";
  throw error;
}

// Integer fractions preserve the same half-up rounding as Decimal on the backend.
function decimal(value, maximum, places, zero = false) {
  const number = Number(value);
  const match = /^(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(String(value));
  if (
    !match ||
    !Number.isFinite(number) ||
    number > maximum ||
    (zero ? number < 0 : number <= 0)
  )
    invalid(
      "Проверьте площадь, ставку и стоимость участка: значения должны быть допустимыми числами.",
    );
  const fraction = match[2] || "",
    exponent = Number(match[3] || 0);
  if (
    places != null &&
    Math.max(0, fraction.replace(/0+$/, "").length - exponent) > places
  )
    invalid("Слишком много знаков после запятой в параметрах стоимости.");
  let n = BigInt(match[1] + fraction),
    d = 1n;
  const power = fraction.length - exponent;
  if (power >= 0) d = 10n ** BigInt(power);
  else n *= 10n ** BigInt(-power);
  return { n, d, value: number };
}

function rounded(n, d = 1n, step = rules.round_to_rub) {
  const unit = BigInt(step);
  return Number(((2n * n + d * unit) / (2n * d * unit)) * unit);
}

export function calculateDemoValuation(
  obj,
  inputs = obj.valuation_inputs || {},
) {
  const allowed = [
    "area_m2",
    "wall_material",
    "unit_cost_rub_m2",
    "rate_note",
    "land_rub",
  ];
  if (Object.keys(inputs).some((key) => !allowed.includes(key)))
    invalid("Неизвестный параметр расчёта стоимости.");
  if (
    inputs.wall_material != null &&
    !Object.hasOwn(rules.rates_rub_m2, inputs.wall_material)
  )
    invalid("Выберите конкретный материал стен из справочника.");
  if (
    inputs.rate_note != null &&
    (typeof inputs.rate_note !== "string" || inputs.rate_note.length > 1000)
  )
    invalid("Источник ставки должен быть строкой до 1000 символов.");
  const project = obj.project || {};
  let material = inputs.wall_material,
    materialSource = material != null ? "manual" : null;
  if (
    !materialSource &&
    Object.hasOwn(rules.rates_rub_m2, obj.material?.observed)
  ) {
    material = obj.material.observed;
    materialSource = "fixed";
  }
  if (
    !materialSource &&
    Object.hasOwn(rules.rates_rub_m2, project.wall_material)
  ) {
    material = project.wall_material;
    materialSource = "project";
  }
  material ??= null;
  let area =
    inputs.area_m2 != null ? decimal(inputs.area_m2, 1000000, 3) : null;
  let areaSource = area ? "manual" : null;
  if (!area && project.total_area_m2 != null) {
    area = decimal(project.total_area_m2, 1000000);
    areaSource = "project_total";
  }
  if (!area && project.footprint_m2 != null && project.floors != null) {
    const footprint = decimal(project.footprint_m2, 1000000),
      floors = decimal(project.floors, 100);
    area = {
      n: footprint.n * floors.n,
      d: footprint.d * floors.d,
      value: footprint.value * floors.value,
    };
    areaSource = "project_footprint_times_floors";
  }
  const manualRate = inputs.unit_cost_rub_m2 != null;
  const rate = manualRate
    ? decimal(inputs.unit_cost_rub_m2, 10000000, 2)
    : material
      ? decimal(rules.rates_rub_m2[material], 10000000, 2)
      : null;
  const hasFlight =
    obj.last_flight?.status === "done" ||
    obj.flights?.some((f) => f.status === "done");
  const readiness =
    obj.last_flight?.readiness_review?.readiness_pct ?? obj.readiness_pct;
  const percent =
    hasFlight &&
    readiness != null &&
    Number.isInteger(readiness) &&
    readiness >= 0 &&
    readiness <= 100
      ? readiness
      : null;
  const missing = [];
  if (!area) missing.push("area_m2");
  if (!rate) missing.push("material_or_unit_cost");
  if (percent == null) missing.push("confirmed_readiness");
  const warnings = [
    "Синтетический демонстрационный сценарий; тарифы не являются рыночными данными.",
    "Условная линейная модель: визуальный процент готовности принят за долю стоимости работ. Это не фактически понесённые расходы.",
  ];
  if (materialSource === "manual")
    warnings.push(
      "Материал выбран только для расчёта; фактический материал стен этим не подтверждается.",
    );
  if (materialSource === "project")
    warnings.push(
      "Материал взят из проекта и пока не подтверждён как фактический.",
    );
  if (areaSource === "project_footprint_times_floors")
    warnings.push("Площадь рассчитана как пятно застройки × этажность.");
  if (manualRate) warnings.push("Использована введённая сотрудником ставка.");
  const land =
    inputs.land_rub != null
      ? decimal(inputs.land_rub, 1000000000000, 2, true)
      : null;
  const landRub = land ? rounded(land.n, land.d, 1) : null;
  let full = null,
    completed = null,
    remaining = null,
    fullRange = null,
    completedRange = null,
    remainingRange = null;
  const range = (amount) => [
    rounded(BigInt(amount) * BigInt(100 - rules.range_percent), 100n),
    rounded(BigInt(amount) * BigInt(100 + rules.range_percent), 100n),
  ];
  if (area && rate) {
    full = rounded(area.n * rate.n, area.d * rate.d);
    fullRange = range(full);
    if (percent != null) {
      completed = Math.min(full, rounded(BigInt(full) * BigInt(percent), 100n));
      remaining = full - completed;
      completedRange = range(completed);
      remainingRange = fullRange.map(
        (value, index) => value - completedRange[index],
      );
    }
  }
  const value = completed == null ? null : completed + (landRub || 0);
  let explanation =
    "Для расчёта укажите площадь и материал или свою ставку за м².";
  const money = (number) => new Intl.NumberFormat("ru-RU").format(number);
  if (full != null) {
    explanation = `${area.value} м² × ${rate.value} ₽/м² = ${money(full)} ₽ для выбранного комплекта работ.`;
    explanation +=
      completed != null
        ? ` При готовности ${percent}%: выполненная часть ≈ ${money(completed)} ₽, остаток ≈ ${money(remaining)} ₽.`
        : " Готовность не подтверждена: выполненная часть и остаток не рассчитаны.";
  }
  return {
    agent: "valuation",
    estimate_type: "construction_cost",
    method: "linear_readiness_assumption",
    rules: rules.version,
    currency: rules.currency,
    status:
      full == null
        ? "insufficient_data"
        : completed == null
          ? "partial"
          : "estimated",
    is_demo: true,
    is_preliminary: true,
    completion_scope: rules.completion_scope,
    excludes: rules.excludes,
    full_cost_rub: full,
    completed_cost_rub: completed,
    remaining_cost_rub: remaining,
    full_range_rub: fullRange,
    completed_range_rub: completedRange,
    remaining_range_rub: remainingRange,
    value_rub: value,
    range_rub: completedRange?.map((amount) => amount + (landRub || 0)) || null,
    value_includes_land: landRub != null,
    range_percent: rules.range_percent,
    round_to_rub: rules.round_to_rub,
    material_rates: Object.fromEntries(
      Object.entries(rules.rates_rub_m2).map(([key, value]) => [
        key,
        Number(value),
      ]),
    ),
    breakdown: {
      area_m2: area?.value ?? null,
      area_source: areaSource,
      footprint_m2: project.footprint_m2,
      floors: project.floors,
      material,
      material_source: materialSource,
      unit_cost_rub_m2: rate?.value ?? null,
      rate_source: manualRate ? "manual" : "configuration",
      rate_note: manualRate ? inputs.rate_note || "" : rules.rate_label,
      readiness_pct: percent,
      readiness_source: percent == null ? null : "demo",
      readiness_status:
        percent != null
          ? "confirmed"
          : hasFlight
            ? "needs_review"
            : "no_flight",
      flight_id: obj.last_flight_id,
      land_rub: landRub,
      land_source: landRub == null ? null : "manual",
    },
    missing_data: missing,
    warnings,
    explanation,
  };
}
