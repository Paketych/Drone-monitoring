import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

import yaml
from pydantic import ValidationError

from agents.valuation.config import ValuationConfig, valuation_config
from schemas.material import MATERIALS
from schemas.valuation import ValuationInputs


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _round(value: Decimal, step: int) -> int:
    return int((value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * step


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _readiness(obj: dict) -> tuple[Decimal | None, str | None, str | None]:
    flight = obj.get("last_flight") or {}
    if flight.get("status") != "done":
        return None, None, None
    review = flight.get("readiness_review")
    raw = flight.get("result") or {}
    if review:
        value, source = review, "employee"
    elif (
        raw.get("status") == "determined"
        and not raw.get("is_demo", True)
        and flight.get("target_confirmed") is True
    ):
        value, source = raw, "model"
    else:
        return None, None, flight.get("flight_id")
    percent = value.get("readiness_pct")
    if percent is None:
        return None, None, flight.get("flight_id")
    number = _decimal(percent)
    if not number.is_finite() or not 0 <= number <= 100:
        return None, None, flight.get("flight_id")
    return number, source, flight.get("flight_id")


def calculate_valuation(
    obj: dict, inputs: dict | None = None, *, config: ValuationConfig | None = None
) -> dict:
    config = config or valuation_config()
    parameters = ValuationInputs.model_validate(inputs or {})
    project = obj.get("project") or {}
    fixed = obj.get("material") or {}
    material = parameters.wall_material
    material_source = "manual" if material is not None else None
    if material_source is None:
        material = fixed.get("observed")
        material_source = "fixed" if material in MATERIALS else None
    if material_source is None:
        material = project.get("wall_material")
        material_source = "project" if material in MATERIALS else None
    if material_source is None:
        material = None
    area = parameters.area_m2
    area_source = "manual" if area is not None else None
    if area is None and project.get("total_area_m2") is not None:
        area, area_source = _decimal(project["total_area_m2"]), "project_total"
    if (
        area is None
        and project.get("footprint_m2") is not None
        and project.get("floors") is not None
    ):
        area = _decimal(project["footprint_m2"]) * _decimal(project["floors"])
        area_source = "project_footprint_times_floors"
    if area is not None and (not area.is_finite() or area <= 0):
        area, area_source = None, None
    rate = parameters.unit_cost_rub_m2
    rate_source = "manual" if rate is not None else "configuration"
    if rate is None and material is not None:
        rate = config.rates_rub_m2[material]
    percent, readiness_source, flight_id = _readiness(obj)
    is_demo = rate_source == "configuration" and config.rate_kind == "demonstration"
    missing = []
    if area is None:
        missing.append("area_m2")
    if rate is None:
        missing.append("material_or_unit_cost")
    if percent is None:
        missing.append("confirmed_readiness")
    warnings = [
        "Условная линейная модель: визуальный процент готовности принят за долю стоимости работ. Это не фактически понесённые расходы."
    ]
    if is_demo:
        warnings.insert(0, "Используются демонстрационные тарифы, не рыночные данные.")
    if material_source == "project":
        warnings.append("Материал взят из проекта и пока не подтверждён как фактический.")
    if material_source == "manual":
        warnings.append(
            "Материал выбран сотрудником для расчёта стоимости. Фактический материал стен этим не подтверждается."
        )
    if area_source == "project_footprint_times_floors":
        warnings.append(
            "Площадь рассчитана как пятно застройки × этажность; это условная площадь по внешнему контуру."
        )
    if rate_source == "manual":
        warnings.append(
            "Использована введённая сотрудником ставка; её происхождение сервис не проверяет."
        )
    full = completed = remaining = value = None
    full_range = completed_range = remaining_range = value_range = None
    land = _round(parameters.land_rub, 1) if parameters.land_rub is not None else None
    status = "insufficient_data"
    if area is not None and rate is not None:
        full = _round(area * rate, config.round_to_rub)
        fraction = Decimal(config.range_percent) / 100
        full_range = [
            _round(_decimal(full) * (1 - fraction), config.round_to_rub),
            _round(_decimal(full) * (1 + fraction), config.round_to_rub),
        ]
        status = "partial"
        if percent is not None:
            completed = min(full, _round(_decimal(full) * percent / 100, config.round_to_rub))
            remaining = full - completed
            completed_range = [
                _round(_decimal(completed) * (1 - fraction), config.round_to_rub),
                _round(_decimal(completed) * (1 + fraction), config.round_to_rub),
            ]
            remaining_range = [
                full_range[0] - completed_range[0],
                full_range[1] - completed_range[1],
            ]
            value = completed + (land or 0)
            value_range = [amount + (land or 0) for amount in completed_range]
            status = "estimated"
    flight = obj.get("last_flight") or {}
    if percent is not None:
        readiness_status = "confirmed"
    elif not flight:
        readiness_status = "no_flight"
    elif flight.get("status") in {"queued", "processing"}:
        readiness_status = "processing"
    elif flight.get("status") == "failed":
        readiness_status = "failed"
    elif (flight.get("result") or {}).get("status") == "model_unavailable":
        readiness_status = "model_unavailable"
    elif flight.get("target_confirmed") is not True:
        readiness_status = "target_not_confirmed"
    else:
        readiness_status = "needs_review"
    breakdown = {
        "area_m2": float(area) if area is not None else None,
        "area_source": area_source,
        "footprint_m2": project.get("footprint_m2"),
        "floors": project.get("floors"),
        "material": material,
        "material_source": material_source,
        "unit_cost_rub_m2": float(rate) if rate is not None else None,
        "rate_source": rate_source,
        "rate_note": parameters.rate_note if rate_source == "manual" else config.rate_label,
        "readiness_pct": float(percent) if percent is not None else None,
        "readiness_source": readiness_source,
        "readiness_status": readiness_status,
        "flight_id": flight_id,
        "land_rub": land,
        "land_source": "manual" if land is not None else None,
    }
    explanation = "Для расчёта нужны площадь, ставка по материалу и подтверждённая готовность."
    if full is not None:
        explanation = f"{area.normalize():f} м² × {rate.normalize():f} ₽/м² = {_money(full)} ₽ для выбранного комплекта работ."
        if completed is not None:
            explanation += f" При готовности {percent.normalize():f}%: выполненная часть ≈ {_money(completed)} ₽, остаток ≈ {_money(remaining)} ₽."
        else:
            explanation += " Готовность не подтверждена: выполненная часть и остаток не рассчитаны."
    identity = {
        "inputs": parameters.model_dump(mode="json"),
        "breakdown": breakdown,
        "config": config.model_dump(mode="json"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    return {
        "agent": "valuation",
        "estimate_type": "construction_cost",
        "method": "linear_readiness_assumption",
        "rules": config.version,
        "currency": "RUB",
        "status": status,
        "is_demo": is_demo,
        "is_preliminary": True,
        "completion_scope": config.completion_scope,
        "excludes": config.excludes,
        "full_cost_rub": full,
        "completed_cost_rub": completed,
        "remaining_cost_rub": remaining,
        "full_range_rub": full_range,
        "completed_range_rub": completed_range,
        "remaining_range_rub": remaining_range,
        "value_rub": value,
        "range_rub": value_range,
        "value_includes_land": land is not None,
        "range_percent": config.range_percent,
        "round_to_rub": config.round_to_rub,
        "material_rates": {key: float(rate) for key, rate in config.rates_rub_m2.items()},
        "breakdown": breakdown,
        "missing_data": missing,
        "warnings": warnings,
        "explanation": explanation,
        "fingerprint": fingerprint,
    }


def valuation_for_object(obj: dict, inputs: dict | None = None) -> dict:
    # An optional cost configuration must never stop photo processing or object access.
    try:
        return calculate_valuation(obj, inputs)
    except (OSError, yaml.YAMLError, ValidationError):
        return {
            "agent": "valuation",
            "estimate_type": "construction_cost",
            "status": "configuration_error",
            "full_cost_rub": None,
            "completed_cost_rub": None,
            "remaining_cost_rub": None,
            "full_range_rub": None,
            "completed_range_rub": None,
            "remaining_range_rub": None,
            "value_rub": None,
            "range_rub": None,
            "breakdown": {},
            "is_preliminary": True,
            "is_demo": True,
            "currency": "RUB",
            "missing_data": ["valuation.configuration"],
            "warnings": ["Тарифы расчёта временно недоступны"],
            "explanation": "Стоимость не рассчитана: нужно проверить настройки тарифов.",
            "fingerprint": None,
        }
