from schemas.material import compare_materials

MATERIAL_STATUSES = {
    "determined": "Материал определён",
    "hidden": "Скрыт отделкой",
    "no_walls": "Стен не обнаружено",
    "insufficient_data": "Недостаточно данных",
    "mixed": "Противоречивые материалы",
    "needs_review": "Нужна проверка",
    "model_unavailable": "Модель недоступна",
}
MATERIAL_REVIEW_REASONS = {
    "TARGET_NOT_CONFIRMED": "Не подтверждено нужное здание",
    "TARGET_OFF_CENTRE": "На части снимков нужный дом не в центре кадра",
    "FEW_CONFIRMED_FACADES": "Недостаточно разных подтверждённых фасадов",
    "LOW_CONFIDENCE": "Недостаточная уверенность",
    "CONFLICTING_MATERIALS": "Ракурсы указывают на разные материалы",
    "PREVIOUS_MATERIAL_CONFLICT": "Результат отличается от закреплённого материала",
    "NO_RELIABLE_WALL_VIEWS": "Нет подходящих снимков открытых стен",
    "CLADDING_MAY_HIDE_STRUCTURE": "Видимый кирпич может быть облицовкой; несущий материал нужно уточнить",
}


def material_result_response(result: dict | None) -> dict | None:
    """Add the shared API fields without changing stored ML observations."""
    if result is None:
        return None
    observations = []
    for observation in result.get("per_view", []):
        observations.append(
            {
                **observation,
                "class": observation.get("material")
                or ("hidden" if observation.get("visibility") == "hidden" else "unclear"),
                "conf": observation.get("detector_confidence")
                if observation.get("detector_confidence") is not None
                else observation.get("confidence", 0),
            }
        )
    return {**result, "per_view": observations}


def fixed_material_response(material: dict | None, project_material: str | None) -> dict | None:
    if material is None:
        return None
    return {
        **material,
        "project": project_material,
        "match": compare_materials(material["observed"], project_material),
    }


def material_data_status(flight: dict | None) -> str:
    """Summarize material processing; this does not assess construction readiness."""
    if flight is None:
        return "no_data"
    if flight["status"] in {"queued", "processing"}:
        return "processing"
    if flight["status"] == "failed":
        return "review"
    if flight.get("material_review") is not None:
        return "ok"
    result = flight["material_result"]
    if result is None or result["status"] == "model_unavailable":
        return "no_data"
    if result["status"] == "determined":
        return "ok"
    return "reshoot" if result.get("needs_reshoot") else "review"


def readiness_data_status(flight: dict | None) -> str:
    if flight is None:
        return "no_data"
    if flight["status"] in {"queued", "processing"}:
        return "processing"
    if flight["status"] == "failed":
        return "review"
    if flight.get("readiness_review") is not None:
        return "ok"
    result = flight.get("result")
    if result is None or result["status"] == "model_unavailable":
        return "no_data"
    return {"needs_reshoot": "reshoot", "needs_review": "review"}.get(result["status"], "ok")


def processing_status(flight: dict | None) -> str:
    statuses = {material_data_status(flight), readiness_data_status(flight)}
    if flight and any(
        error["code"] != "MODEL_NOT_CONFIGURED"
        for error in flight.get("module_errors", {}).values()
    ):
        statuses.add("review")
    return next(
        value for value in ("processing", "reshoot", "review", "ok", "no_data") if value in statuses
    )
