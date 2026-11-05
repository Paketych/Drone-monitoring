import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from schemas.analysis import Recommendation

RULES_VERSION = "inspection-analysis@v2"
PROMPT_VERSION = "inspection-summary@v2"
STAGES = ("none", "foundation", "walls", "roof", "openings", "facade", "done")
STEP_LABELS = {
    "FLIGHT_TASK": "Запланировать контрольный облёт",
    "MANUAL_INSPECTION": "Проверить данные объекта",
    "COMM_REASON": "Уточнить причину отставания у клиента",
    "SMS_TERM_REMINDER": "Напомнить о плановой дате",
}
SYSTEM_PROMPT = (
    "Ты готовишь краткую сводку инспекции дома на русском языке. "
    "Используй только facts: они уже рассчитаны бэкендом. "
    "Выбери только пару strategy_code/next_step_code из allowed_pairs. "
    "Причина должна опираться на evidence_refs; включи все required_refs. "
    "Верни missing_data точно из переданного списка. "
    "Не меняй проценты и даты, не выдумывай завершённые работы, причины задержки, "
    "задолженность или исходы общения. Недостаток данных не означает нарушение клиентом. "
    "Не предлагай судебные, кредитные или залоговые решения. "
    "Суммы budget рассчитаны кодом: не пересчитывай их. При budget.is_demo=true "
    "называй тарифы демонстрационными. Это условная стоимость строительства, "
    "не рыночная цена, не цена залога и не подтверждённые расходы клиента. "
    "Ты не исполняешь действия и не подтверждаешь решения за сотрудника. "
    "Верни только JSON по переданной схеме."
)


def _date(value) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def build_context(obj: dict, *, today: date | None = None, utc_offset: int = 7) -> dict:
    """Only whitelisted scalar facts leave the server; no comments or identifiers."""
    today = today or datetime.now(timezone(timedelta(hours=utc_offset))).date()
    flight = obj.get("last_flight") or {}
    ready = flight.get("result") or {}
    review = flight.get("readiness_review")
    trusted = bool(review) or (
        ready.get("status") == "determined"
        and not ready.get("is_demo", True)
        and flight.get("target_confirmed") is True
    )
    effective = (review or ready) if trusted else {}
    fixed = obj.get("material") or {}
    budget = obj.get("valuation") or {}
    inspection_date = _date(flight.get("flown_at") or flight.get("uploaded_at"))
    as_of = min(inspection_date, today) if inspection_date else today
    start, end = _date(obj.get("started_at")), _date(obj.get("planned_completion_at"))
    checkpoints = sorted(obj.get("checkpoints") or [], key=lambda point: point["due"])
    due = [point for point in checkpoints if _date(point["due"]) <= as_of]
    expected_stage = due[-1]["stage"] if due else None
    expected_pct = None
    gap = None
    lag = None
    basis = "missing"
    if checkpoints:
        basis = "checkpoints"
        if expected_stage and effective.get("stage"):
            lag = STAGES.index(effective["stage"]) < STAGES.index(expected_stage)
        elif trusted:
            lag = False  # No checkpoint has come due yet.
    elif start is not None and end is not None and end > start:
        basis = "linear_assumption"
        expected_pct = round(100 * max(0, min(1, (as_of - start).days / (end - start).days)))
        if effective.get("readiness_pct") is not None:
            gap = expected_pct - effective["readiness_pct"]
            lag = gap >= 10
    facts = {
        "snapshot.date": today.isoformat(),
        "inspection.has_flight": bool(flight),
        "inspection.processing": flight.get("status") in {"queued", "processing"},
        "inspection.date": inspection_date.isoformat() if inspection_date else None,
        "inspection.stale": inspection_date is not None and (today - inspection_date).days > 30,
        "inspection.future_dated": inspection_date is not None and inspection_date > today,
        "inspection.target_confirmed": bool(flight.get("target_confirmed")),
        "readiness.trusted": trusted,
        "readiness.reviewed": bool(review),
        "readiness.status": ready.get("status"),
        "readiness.stage": effective.get("stage"),
        "readiness.readiness_pct": effective.get("readiness_pct"),
        "readiness.level": effective.get("readiness_level"),
        "material.observed": fixed.get("observed"),
        "material.project": (obj.get("project") or {}).get("wall_material"),
        "material.match": fixed.get("match"),
        "material.reviewed": bool(fixed.get("reviewed")),
        "term.planned_completion_at": end.isoformat() if end else None,
        "term.expired": end is not None and today > end,
        "registry.overdue": obj.get("overdue"),
        "registry.registered": obj.get("registered"),
        "registry.pledged": obj.get("pledged"),
        "plan.basis": basis,
        "plan.as_of_date": as_of.isoformat(),
        "plan.expected_stage": expected_stage,
        "plan.expected_pct": expected_pct,
        "plan.gap_pp": gap,
        "plan.lag_suspected": lag,
        "budget.status": budget.get("status"),
        "budget.completion_scope": budget.get("completion_scope"),
        "budget.method": budget.get("method"),
        "budget.is_demo": budget.get("is_demo"),
        "budget.full_cost_rub": budget.get("full_cost_rub"),
        "budget.completed_cost_rub": budget.get("completed_cost_rub"),
        "budget.remaining_cost_rub": budget.get("remaining_cost_rub"),
    }
    missing = []
    if not trusted:
        missing.append("readiness.confirmation")
    if basis == "missing":
        missing.append("plan.schedule")
    if not fixed:
        missing.append("material.confirmation")
    if facts["inspection.processing"]:
        pair, refs, reason = (
            [None, None],
            ["inspection.processing"],
            "Фотографии ещё обрабатываются. Заключение нужно обновить после завершения.",
        )
    elif facts["inspection.future_dated"]:
        pair, refs, reason = (
            [None, "MANUAL_INSPECTION"],
            ["inspection.future_dated"],
            "Дата съёмки находится в будущем. Нужно проверить дату и метаданные снимков.",
        )
    elif not flight or facts["inspection.stale"]:
        pair, refs, reason = (
            ["S1", "FLIGHT_TASK"],
            ["inspection.has_flight" if not flight else "inspection.stale"],
            "Для оценки текущего состояния нужны актуальные фотографии дома.",
        )
    elif not trusted:
        pair, refs, reason = (
            [None, "MANUAL_INSPECTION"],
            ["readiness.trusted"],
            "Готовность пока не подтверждена. Требуется проверка снимков и целевого здания сотрудником.",
        )
    elif fixed.get("match") in {"mismatch", "deviation"}:
        pair, refs, reason = (
            [None, "MANUAL_INSPECTION"],
            ["material.match"],
            "Зафиксировано отличие материала от проекта. Сотруднику нужно проверить документы и снимки.",
        )
    elif lag is True or (facts["term.expired"] and effective.get("readiness_level") != 100):
        pair, refs = (
            [None, "COMM_REASON"],
            ["plan.lag_suspected"] if lag else ["term.expired", "readiness.level"],
        )
        reason = "Есть признак отставания от графика. Следует уточнить ситуацию у клиента; причина задержки по снимкам не устанавливается."
        if basis == "linear_assumption":
            reason += " Сравнение использует условный линейный план и требует проверки."
    elif basis == "missing":
        pair, refs, reason = (
            [None, "MANUAL_INSPECTION"],
            ["plan.basis"],
            "Без графика нельзя оценить соблюдение сроков. Нужно уточнить план строительства.",
        )
    else:
        pair, refs, reason = (
            ["S5", "SMS_TERM_REMINDER"],
            ["readiness.trusted", "plan.lag_suspected"],
            "По доступному сравнению признак отставания не выявлен. Продолжить мониторинг и напомнить о плановой дате.",
        )
    # The model explains this bounded decision; it cannot invent financial strategies.
    return {
        "facts": facts,
        "allowed_pairs": [pair],
        "required_refs": refs,
        "missing_data": missing,
        "fallback": {
            "strategy_code": pair[0],
            "next_step_code": pair[1],
            "reason": reason,
            "evidence_refs": refs,
            "missing_data": missing,
        },
        "rules_version": RULES_VERSION,
        "prompt_version": PROMPT_VERSION,
    }


def fingerprint(context: dict) -> str:
    return hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def response_schema(context: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["strategy_code", "next_step_code", "reason", "evidence_refs", "missing_data"],
        "properties": {
            "strategy_code": {
                "enum": sorted({pair[0] for pair in context["allowed_pairs"]}, key=str)
            },
            "next_step_code": {
                "enum": sorted({pair[1] for pair in context["allowed_pairs"]}, key=str)
            },
            "reason": {"type": "string"},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string", "enum": list(context["facts"])},
            },
            "missing_data": {
                "type": "array",
                "items": {
                    "type": "string",
                    **({"enum": context["missing_data"]} if context["missing_data"] else {}),
                },
                **({"maxItems": 0} if not context["missing_data"] else {}),
            },
        },
    }


def validate_recommendation(content: str, context: dict) -> dict:
    recommendation = Recommendation.model_validate_json(content)
    if [recommendation.strategy_code, recommendation.next_step_code] not in context[
        "allowed_pairs"
    ]:
        raise ValueError("DISALLOWED_DECISION")
    refs = recommendation.evidence_refs
    if (
        len(refs) != len(set(refs))
        or not set(context["required_refs"]) <= set(refs)
        or not set(refs) <= context["facts"].keys()
    ):
        raise ValueError("INVALID_EVIDENCE")
    if len(recommendation.missing_data) != len(set(recommendation.missing_data)) or set(
        recommendation.missing_data
    ) != set(context["missing_data"]):
        raise ValueError("INVALID_MISSING_DATA")
    return recommendation.model_dump()
