from collections import defaultdict
from math import prod

from agents.geo import nadir_spread_m
from agents.readiness.config import classes_config, stages_config
from agents.readiness.parcel import outside_parcel
from schemas.material import FACADES, TOP_VIEWS
from schemas.readiness import (
    STAGE_CLASSES,
    Detection,
    Element,
    ReadinessResult,
    Stage,
    default_bank_readiness,
)
from schemas.readiness import DetectorClass as C

STAGE_ELEMENTS = {
    Stage.FOUNDATION: (C.FOUNDATION,),
    Stage.WALLS: (C.WALLS_PARTIAL, C.WALLS_FULL),
    Stage.ROOF: (C.ROOF_FRAME, C.ROOF_COVERED),
    Stage.OPENINGS: (C.WINDOW_INSTALLED, C.DOOR_INSTALLED),
    Stage.FACADE: (C.FACADE_FINISH,),
}
# A later element proves the earlier ones even when they are hidden from the camera.
IMPLIES = {
    C.WALLS_PARTIAL: (C.FOUNDATION,),
    C.WALLS_FULL: (C.FOUNDATION,),
    C.ROOF_FRAME: (C.WALLS_FULL, C.FOUNDATION),
    C.ROOF_COVERED: (C.ROOF_FRAME, C.WALLS_FULL, C.FOUNDATION),
    C.WINDOW_INSTALLED: (C.WALLS_FULL, C.FOUNDATION),
    C.DOOR_INSTALLED: (C.WALLS_FULL, C.FOUNDATION),
    C.FACADE_FINISH: (C.WALLS_FULL, C.FOUNDATION),
}
ALL_GROUPS = {"facade", "top_low", "top_high"}


def view_group(image: dict) -> tuple[str | None, str]:
    """Return the view group for the class mask and the key of independent evidence.

    Repeated photographs of one facade share a key, so they do not count as extra evidence.
    """
    view = image.get("view")
    if view in FACADES:
        return "facade", view
    if view in {"top_low", "top_high"}:
        return view, view
    if image.get("is_overhead"):
        return "top", "top:" + image["sha256"]
    if image.get("camera_pitch") is not None:
        return "facade", "facade:" + image["sha256"]
    return None, "image:" + image["sha256"]


def counts_for(cls: str, group: str | None) -> bool:
    allowed = classes_config().allowed(cls)
    if group == "top":
        return {"top_low", "top_high"} <= allowed
    if group is None:
        return ALL_GROUPS <= allowed
    return group in allowed


def annotate(detections: list[Detection], images: list[dict]) -> dict[str, list[dict]]:
    """Detections per image for the API, marking those excluded by the view mask."""
    labels = {item.id: item.label for item in classes_config().classes}
    image_by_id = {image["id"]: image for image in images}
    result: dict[str, list[dict]] = {image["id"]: [] for image in images}
    for detection in detections:
        group, _ = view_group(image_by_id[detection.image_id])
        result[detection.image_id].append(
            {
                **detection.model_dump(mode="json", by_alias=True, exclude={"image_id"}),
                "label": labels.get(detection.cls, str(detection.cls)),
                "counted": detection.target_included
                and detection.cls in STAGE_CLASSES
                and counts_for(detection.cls, group),
            }
        )
    return result


def plural(count: int, forms: tuple[str, str, str]) -> str:
    if count % 10 == 1 and count % 100 != 11:
        form = forms[0]
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"


def aggregate(
    detections: list[Detection],
    images: list[dict],
    *,
    model: str,
    is_demo: bool,
    target_confirmed: bool,
    parcel: dict | None = None,
) -> ReadinessResult:
    """Turn detections from all views into a stage, readiness % and the bank scale."""
    rules = stages_config()
    result = ReadinessResult(
        model=model,
        rules=rules.version,
        is_demo=is_demo,
        status="model_unavailable",
        explanation=(
            "Деморежим: снимки сохранены, обученный детектор стадий не подключён. "
            "Стадия по фотографиям не определялась."
        ),
    )
    if is_demo:
        return result

    threshold = rules.present_threshold
    image_by_id = {image["id"]: image for image in images}
    groups = {image["id"]: view_group(image) for image in images}
    facade_keys = {key for group, key in groups.values() if group == "facade"}
    sides = {image["view"] for image in images if image.get("view") in FACADES}
    views = {image.get("view") for image in images}

    # Strongest detection of each class per independent key, after the view mask.
    best: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    per_image_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for detection in detections:
        group, key = groups[detection.image_id]
        if (
            not detection.target_included
            or detection.cls not in STAGE_CLASSES
            or not counts_for(detection.cls, group)
        ):
            continue
        if detection.conf < rules.evidence_min_conf:
            continue
        if detection.conf > best[detection.cls].get(key, (0.0, ""))[0]:
            best[detection.cls][key] = (detection.conf, detection.image_id)
        if detection.conf >= threshold and detection.cls == C.WINDOW_INSTALLED:
            per_image_counts[detection.image_id][0] += 1
        if detection.conf >= threshold and detection.cls == C.OPENING_EMPTY:
            per_image_counts[detection.image_id][1] += 1

    # Noisy-OR over independent keys: an element seen on three facades is more reliable.
    probability = {
        cls: 1 - prod(1 - conf for conf, _ in best[cls].values()) for cls in STAGE_CLASSES
    }
    present = {cls: probability[cls] >= threshold for cls in STAGE_CLASSES}
    implied = {cls: False for cls in STAGE_CLASSES}
    for cls, earlier in IMPLIES.items():
        if present[cls]:
            for item in earlier:
                implied[item] = implied[item] or not present[item]

    def has(cls: str) -> bool:
        return present[cls] or implied[cls]

    def support(cls: str) -> list[tuple[str, str]]:
        return [(key, image_id) for key, (conf, image_id) in best[cls].items() if conf >= threshold]

    for cls in STAGE_CLASSES:
        supporting = support(cls)
        result.elements[cls] = Element(
            present=has(cls),
            implied=implied[cls],
            conf=round(probability[cls], 4) if best[cls] else None,
            views=sorted({image_by_id[image_id]["view"] for _, image_id in supporting} - {None}),
            image_ids=[image_id for _, image_id in supporting],
        )

    # Each facade counts once: take its photograph with the most visible openings.
    windows = openings = 0
    for key in facade_keys:
        counts = [
            per_image_counts[image_id]
            for image_id, (group, image_key) in groups.items()
            if image_key == key
        ]
        installed, empty = max(counts, key=sum, default=[0, 0])
        windows, openings = windows + installed, openings + empty
    ratio = windows / (windows + openings) if windows + openings else 0.0
    finished = {key for key, _ in support(C.FACADE_FINISH) if key in facade_keys}
    facade_share = len(finished) / len(facade_keys) if facade_keys else 0.0
    result.windows_ratio = round(ratio, 4)
    result.facades_seen = len(sides)

    stage = Stage.NONE
    for candidate, elements in STAGE_ELEMENTS.items():
        if any(present[cls] for cls in elements):
            stage = candidate
    door = present[C.DOOR_INSTALLED]
    complete_openings = present[C.ROOF_COVERED] and ratio >= rules.done_windows_ratio and door
    if complete_openings and facade_keys and facade_share == 1:
        stage = Stage.DONE

    weights = rules.weights
    if stage == Stage.NONE:
        pct = weights.site_prep if present[C.SITE_PREP] else 0.0
    else:
        pct = weights.foundation if has(C.FOUNDATION) else 0.0
        if has(C.WALLS_FULL):
            pct += weights.walls
        elif present[C.WALLS_PARTIAL]:
            pct += weights.walls / 2
        if present[C.ROOF_COVERED]:
            pct += weights.roof
        elif has(C.ROOF_FRAME):
            pct += weights.roof / 2
        pct += weights.windows * ratio + (weights.door if door else 0)
        pct += weights.facade * facade_share
        pct += weights.done if stage == Stage.DONE else 0
    result.stage = stage
    result.stage_label = rules.stage_label(stage)
    result.readiness_pct = round(pct)
    result.bank_readiness = 100 if complete_openings else default_bank_readiness(stage)

    # Confidence: the current stage is visible and nothing from later stages is.
    order = list(Stage)
    if stage == Stage.NONE:
        current = 1.0
    elif stage == Stage.DONE:
        current = min(probability[cls] for cls in (C.ROOF_COVERED, C.DOOR_INSTALLED))
    else:
        current = max(probability[cls] for cls in STAGE_ELEMENTS[stage] if present[cls])
    later = [
        probability[cls]
        for candidate, elements in STAGE_ELEMENTS.items()
        if order.index(candidate) > order.index(stage)
        for cls in elements
    ]
    result.confidence = round(min(current, 1 - max(later, default=0.0)), 4)

    reasons = []
    if "top_low" not in views:
        reasons.append("NO_TOP_LOW")
    if "top_high" not in views:
        reasons.append("NO_TOP_HIGH")
    if len(sides) < rules.min_facades:
        reasons.append("FEW_FACADES")
    # Over a nadir the drone hangs above the house (0-5 m apart on dataset/); side shots are
    # taken 12-38 m away, so only nadirs say whether the flight was over the parcel.
    located = [
        image["gps"]
        for image in images
        if image.get("gps") and (image.get("is_overhead") or image.get("view") in TOP_VIEWS)
    ]
    # Without a parcel boundary or GPS there is nothing to compare; target_confirmed covers it.
    if parcel and located and outside_parcel(parcel, located, rules.parcel_buffer_m):
        reasons.append("OUT_OF_PARCEL")
    spread = nadir_spread_m(images)
    if rules.max_nadir_spread_m is not None and spread is not None:
        if spread > rules.max_nadir_spread_m:
            reasons.append("MIXED_LOCATIONS")
    if result.confidence < rules.low_confidence:
        reasons.append("LOW_CONFIDENCE")
    on_top = best[C.ROOF_COVERED].get("top_low", (0.0, ""))[0] >= threshold

    def on_facades(cls: str) -> bool:
        return any(key in facade_keys for key, _ in support(cls))

    if on_top and on_facades(C.ROOF_FRAME) and not on_facades(C.ROOF_COVERED):
        reasons.append("VIEWS_CONFLICT")
    if any(image.get("quality_issues") for image in images):
        reasons.append("BAD_IMAGE")
    result.reshoot_reasons = reasons
    result.needs_reshoot = bool(reasons)
    if not target_confirmed:
        result.review_reasons.append("TARGET_NOT_CONFIRMED")
    off_centre = sum(image.get("target_region_source") == "centre_zone" for image in images)
    if off_centre:
        # Houses were seen but none in the centre: the target was not guessed on those frames.
        result.review_reasons.append("TARGET_OFF_CENTRE")
    if result.needs_reshoot:
        result.status = "needs_reshoot"
    elif result.review_reasons:
        result.status = "needs_review"
    else:
        result.status = "determined"
    result.explanation = explain(result, stage, has, support, facade_share, len(facade_keys))
    if off_centre:
        result.explanation += (
            f" На {plural(off_centre, ('снимке', 'снимках', 'снимках'))} нужный дом не в центре"
            " кадра: соседние дома не учитывались, проверьте результат."
        )
    return result


def explain(result, stage, has, support, facade_share, facade_count) -> str:
    """Template explanation from the rules that fired, without an LLM."""

    def seen(cls: str) -> str:
        return plural(len(support(cls)), ("ракурс", "ракурса", "ракурсов"))

    parts = []
    if stage == Stage.NONE:
        parts.append("признаков строительства не найдено")
        if result.elements[C.SITE_PREP].present:
            parts.append(f"есть подготовка участка ({seen(C.SITE_PREP)})")
    else:
        foundation = result.elements[C.FOUNDATION]
        if not foundation.implied:
            parts.append(f"фундамент найден ({seen(C.FOUNDATION)})")
        walls = result.elements[C.WALLS_FULL]
        if walls.present:
            parts.append(
                "стены выведены (по более поздним этапам)"
                if walls.implied
                else f"стены выведены ({seen(C.WALLS_FULL)})"
            )
        elif result.elements[C.WALLS_PARTIAL].present:
            parts.append(f"стены в процессе ({seen(C.WALLS_PARTIAL)})")
        elif stage == Stage.FOUNDATION:
            parts.append("стен нет")
        if result.elements[C.ROOF_COVERED].present:
            parts.append(f"кровля покрыта ({seen(C.ROOF_COVERED)})")
        elif has(C.ROOF_FRAME):
            parts.append("стропила установлены, кровельного покрытия нет")
        elif has(C.WALLS_FULL) or has(C.WALLS_PARTIAL):
            parts.append("кровли нет")
        if result.windows_ratio or support(C.OPENING_EMPTY):
            parts.append(f"окна установлены в {round(result.windows_ratio * 100)}% проёмов")
        if result.elements[C.DOOR_INSTALLED].present:
            parts.append("входная дверь есть")
        elif stage != Stage.FOUNDATION:
            parts.append("входная дверь не найдена")
        if facade_share:
            done = round(facade_share * facade_count)
            parts.append(f"отделка на {done} из {facade_count} снятых фасадов")
        if stage == Stage.DONE:
            parts.append("дом внешне завершён, внутреннюю отделку и коммуникации с дрона не видно")
    text = ", ".join(parts)
    text = text[0].upper() + text[1:] + "."
    labels = stages_config().reshoot_reasons
    if result.reshoot_reasons:
        text += (
            " Нужна пересъёмка: "
            + "; ".join(labels.get(code, code) for code in result.reshoot_reasons)
            + "."
        )
    if "TARGET_NOT_CONFIRMED" in result.review_reasons:
        text += " Не подтверждено, что на снимках нужный дом."
    return text
