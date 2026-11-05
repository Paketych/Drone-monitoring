"""Classify original-resolution wall crops provided by the shared detector."""

import os
from pathlib import Path

from agents.material.config import material_config
from agents.targeting import overlap_fraction
from schemas.material import MATERIALS, Observation


def padded_bbox(box: tuple) -> tuple:
    x1, y1, x2, y2 = box
    margin = material_config().crop_margin
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    return max(0, x1 - mx), max(0, y1 - my), min(1, x2 + mx), min(1, y2 + my)


class YoloMaterialPredictor:
    is_demo = False

    def __init__(self, weights: Path):
        if not weights.is_file():
            raise ValueError("Укажите существующий локальный файл MATERIAL_WEIGHTS")
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.rules = material_config()
        self.names = (
            dict(enumerate(self.model.names))
            if isinstance(self.model.names, list)
            else self.model.names
        )
        if not self.names or not set(self.names.values()) <= set(MATERIALS) | {"hidden", "unclear"}:
            raise ValueError("Неизвестные имена материалов в весах классификатора")
        if getattr(self.model, "task", "classify") != "classify":
            raise ValueError("MATERIAL_WEIGHTS должны содержать классификатор")
        self.version = "yolo-material:" + weights.name

    def predict_walls(self, image, image_id: str, detections: list) -> list[Observation]:
        walls = [
            row for row in detections if row.cls in self.rules.wall_classes and row.target_included
        ]
        if not walls:
            return [Observation(image_id=image_id, visibility="no_walls")]
        width, height = image.size
        observations = []
        for wall in walls:
            crop_box = padded_bbox(wall.bbox)
            x1, y1, x2, y2 = crop_box
            if min((x2 - x1) * width, (y2 - y1) * height) < self.rules.min_crop_px:
                # The classifier never saw such crops in training; keep the wall as unclear.
                observations.append(
                    Observation(
                        image_id=image_id,
                        cls=wall.cls,
                        bbox=wall.bbox,
                        crop_bbox=crop_box,
                        detector_confidence=wall.conf,
                        material="unclear",
                        visibility="unclear",
                    )
                )
                continue
            with image.crop((x1 * width, y1 * height, x2 * width, y2 * height)) as crop:
                probs = self.model.predict(source=crop, imgsz=384, verbose=False)[0].probs
            if probs is None:
                raise ValueError("Классификатор не вернул вероятности материала")
            predicted_material = self.names[int(probs.top1)]
            confidence = float(probs.top1conf)
            material = (
                predicted_material if confidence >= self.rules.min_crop_confidence else "unclear"
            )
            reasons = []
            if material == "brick" and any(
                row.cls == "facade_finish"
                and row.target_included
                and row.conf >= self.rules.min_cladding_confidence
                and overlap_fraction(wall.bbox, row.bbox) >= self.rules.min_cladding_overlap
                for row in detections
            ):
                reasons.append("CLADDING_MAY_HIDE_STRUCTURE")
            observations.append(
                Observation(
                    image_id=image_id,
                    cls=wall.cls,
                    bbox=wall.bbox,
                    crop_bbox=crop_box,
                    detector_confidence=wall.conf,
                    material_conf=confidence,
                    predicted_material=predicted_material,
                    confidence=confidence,
                    material=material,
                    visibility=material if material in {"hidden", "unclear"} else "visible",
                    review_reasons=reasons,
                )
            )
        return observations


def create_predictor() -> YoloMaterialPredictor:
    return YoloMaterialPredictor(Path(os.getenv("MATERIAL_WEIGHTS", "")))
