export function normalizeMeta(raw) {
  if (!Array.isArray(raw?.materials) || !Array.isArray(raw?.views))
    throw new Error("В /meta отсутствуют материалы или ракурсы.");
  const legacy =
    !raw.capabilities &&
    Array.isArray(raw.risk_levels) &&
    Array.isArray(raw.steps);
  const capabilities = raw.capabilities || {
    wall_material: true,
    material_review: !legacy,
    readiness: Boolean(raw.stages?.length),
    readiness_review: legacy,
    risk: legacy,
    conductor: legacy,
    alerts: legacy,
    tasks: legacy,
    history: legacy,
    actions: legacy,
    registry_import: legacy,
    land: legacy,
    valuation: legacy,
    act: legacy,
  };
  const result = { ...raw, capabilities };
  for (const key of [
    "risk_levels",
    "steps",
    "strategies",
    "signals",
    "classes",
    "stages",
    "bank_readiness",
    "material_statuses",
  ])
    result[key] = Array.isArray(raw[key]) ? raw[key] : [];
  return result;
}

export const supports = (meta, name) => meta?.capabilities?.[name] === true;

export const percent = (value) =>
  Number.isFinite(value) ? `${Math.round(value * 100)}%` : "Не указана";

export function selectableMaterials(meta) {
  return meta.materials.filter(
    (item) => !["hidden", "unclear"].includes(item.id),
  );
}
