"""Общие пути и чтение разметки для скриптов ml/.

Строка метки: `class cx cy w h [material]` — стандартный YOLO плюс необязательный материал стены
(configs/materials.yaml). prepare_dataset.py убирает материал при сборке датасета для детектора.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

import yaml

REPO = Path(__file__).resolve().parent.parent
CLASSES_YAML = REPO / "configs" / "classes.yaml"
MATERIALS_YAML = REPO / "configs" / "materials.yaml"
ANNOTATIONS = REPO / "ml" / "annotations"
LABELS_DIR = ANNOTATIONS / "labels"
MANIFEST = ANNOTATIONS / "manifest.csv"

# Облёт по схеме (раздел 3.1) и ракурсы внешних фото (ml/add_images.py).
VIEWS = ["N", "E", "S", "W", "top_low", "top_high", "oblique", "nadir", "ground"]

# Статус снимка: в обучение идут только reviewed (prepare_dataset.py).
STATUS_REVIEWED = "reviewed"
STATUS_PRELABEL = "prelabel"  # рамки модели, человек ещё не проверял
STATUS_SKIP = "skip"  # снимок не берём


class Box(NamedTuple):
    cls: int
    cx: float
    cy: float
    w: float
    h: float
    material: str = ""


def load_classes() -> list[dict]:
    """Классы из configs/classes.yaml, отсортированные по id без пропусков."""
    classes = yaml.safe_load(CLASSES_YAML.read_text(encoding="utf-8"))["classes"]
    classes.sort(key=lambda c: c["id"])
    if [c["id"] for c in classes] != list(range(len(classes))):
        raise ValueError(f"{CLASSES_YAML}: id классов должны идти подряд с 0")
    return classes


def load_materials() -> tuple[list[str], list[str]]:
    """(материалы, классы стен, к которым они относятся)."""
    cfg = yaml.safe_load(MATERIALS_YAML.read_text(encoding="utf-8"))
    return [m["name"] for m in sorted(cfg["materials"], key=lambda m: m["id"])], cfg["wall_classes"]


def read_manifest() -> list[dict]:
    with MANIFEST.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_manifest(rows: list[dict]) -> None:
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with MANIFEST.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)


def label_path(image: str) -> Path:
    """dataset/4/DJI_x.JPG -> ml/annotations/labels/4/DJI_x.txt; data/external/house_photos/house_001/001.jpg -> labels/house_001/001.txt"""
    p = Path(image)
    return LABELS_DIR / p.parent.name / f"{p.stem}.txt"


def read_labels(image: str) -> list[Box]:
    """Рамки YOLO в долях кадра, с материалом стены, если он размечен."""
    path = label_path(image)
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c, cx, cy, w, h, *rest = line.split()
            boxes.append(
                Box(int(c), float(cx), float(cy), float(w), float(h), rest[0] if rest else "")
            )
    return boxes


def write_labels(image: str, boxes: list[Box] | list[tuple]) -> None:
    path = label_path(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for b in map(lambda x: Box(*x), boxes):
        line = f"{b.cls} {b.cx:.6f} {b.cy:.6f} {b.w:.6f} {b.h:.6f}"
        lines.append(f"{line} {b.material}\n" if b.material else f"{line}\n")
    path.write_text("".join(lines), encoding="utf-8")
