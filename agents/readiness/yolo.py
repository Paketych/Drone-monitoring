"""Optional inference adapter; training stays with the ML team."""

import os
from pathlib import Path

from agents.readiness.config import classes_config
from schemas.readiness import Detection


class YoloDetector:
    is_demo = False

    def __init__(self, weights: Path) -> None:
        if not weights.is_file():
            raise ValueError("Укажите существующий локальный файл READINESS_WEIGHTS")
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.names = (
            dict(enumerate(self.model.names))
            if isinstance(self.model.names, list)
            else self.model.names
        )
        known = {item.id for item in classes_config().classes}
        if not set(self.names.values()) <= known:
            raise ValueError("Названия классов в весах не совпадают с configs/classes.yaml")
        self.version = "yolo:" + weights.name

    def detect(self, image, image_id: str) -> list[Detection]:
        result = self.model.predict(source=image, imgsz=1280, verbose=False)[0]
        if result.boxes is None:
            return []
        boxes = result.boxes
        return [
            Detection(image_id=image_id, cls=self.names[int(index)], conf=confidence, bbox=box)
            for index, confidence, box in zip(
                boxes.cls.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.xyxyn.cpu().tolist()
            )
        ]


def create_detector() -> YoloDetector:
    return YoloDetector(Path(os.getenv("READINESS_WEIGHTS", "")))
