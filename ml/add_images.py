"""Добавить партию внешних фото в разметку (ml/annotations/manifest.csv).

    python ml/add_images.py data/external/house_photos_2
    python ml/add_images.py data/external/house_photos --max-per-house 3
    python ml/add_images.py data/external/house_photos_2 --prelabel weights/readiness_yolo26m.pt

Партия — папка house_NNN/*.jpg с manifest.csv по ТЗ на сбор фото. Каждая папка house_NNN — отдельный объект
для деления train/val. Новые снимки получают статус prelabel: в обучение они не идут, пока разметку
не проверят (Label Studio или вручную). С --prelabel обученная модель ставит черновые рамки, чтобы
разметчик правил их, а не рисовал с нуля. Уже добавленные снимки пропускаются.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from common import (
    REPO,
    STATUS_PRELABEL,
    STATUS_SKIP,
    Box,
    label_path,
    read_manifest,
    write_labels,
    write_manifest,
)
from PIL import Image

VIEW_MAP = {
    "drone_nadir": "nadir",
    "drone_oblique": "oblique",
    "elevated": "oblique",
    "ground": "ground",
}
# Стадии первой партии записаны номерами пунктов ТЗ.
STAGE_IDS = {
    "1": "roof_frame",
    "2": "walls_partial",
    "3": "foundation",
    "4": "open_walls",
    "5": "openings_empty",
}
# При --max-per-house первыми берутся снимки редких стадий.
STAGE_PRIORITY = [
    "roof_frame",
    "walls_partial",
    "empty_plot",
    "site_prep",
    "foundation",
    "openings_empty",
    "open_walls",
    "walls_full",
    "roof_covered",
    "finished",
]


def _stages(row: dict) -> list[str]:
    raw = row.get("stages") or row.get("stage_ids") or ""
    return [STAGE_IDS.get(s, s) for s in raw.split(";") if s]


def _priority(row: dict) -> int:
    return min(
        (STAGE_PRIORITY.index(s) for s in _stages(row) if s in STAGE_PRIORITY),
        default=len(STAGE_PRIORITY),
    )


def register(batch: Path, max_per_house: int) -> list[str]:
    """Дописывает снимки партии в manifest.csv и возвращает пути новых снимков со статусом prelabel."""
    with (batch / "manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        src_rows = list(csv.DictReader(f))
    rows = read_manifest()
    known = {r["image"] for r in rows}
    by_house: dict[str, list[dict]] = {}
    for r in src_rows:
        by_house.setdefault(r["house_id"], []).append(r)

    added = []
    for house, items in by_house.items():
        keep = (
            {id(r) for r in sorted(items, key=_priority)[:max_per_house]}
            if max_per_house
            else {id(r) for r in items}
        )
        for r in items:
            image = (batch / house / r["file"]).resolve().relative_to(REPO).as_posix()
            if image in known:
                continue
            w, h = Image.open(REPO / image).size
            status = STATUS_PRELABEL if id(r) in keep else STATUS_SKIP
            rows.append(
                {
                    "image": image,
                    "object": house,
                    "view": VIEW_MAP.get(r.get("view", ""), "ground"),
                    "width": w,
                    "height": h,
                    "boxes": 0,
                    "note": f"{', '.join(_stages(r))}; стены: {r.get('wall_material') or 'unknown'}",
                    "status": status,
                    "source": r.get("source_page", ""),
                }
            )
            if status == STATUS_PRELABEL:
                added.append(image)
    write_manifest(rows)
    print(f"добавлено снимков: {len(added)} на разметку, всего в партии {len(src_rows)}")
    return added


def prelabel(images: list[str], weights: Path, conf: float, imgsz: int) -> None:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    todo = [i for i in images if not label_path(i).exists()]
    counts = {}
    for k in range(0, len(todo), 16):
        chunk = todo[k : k + 16]
        for image, res in zip(
            chunk,
            model.predict([str(REPO / i) for i in chunk], conf=conf, imgsz=imgsz, verbose=False),
        ):
            boxes = [
                Box(int(c), *map(float, xywh)) for c, xywh in zip(res.boxes.cls, res.boxes.xywhn)
            ]
            write_labels(image, boxes)
            counts[image] = len(boxes)
    rows = read_manifest()
    for r in rows:
        if r["image"] in counts:
            r["boxes"] = counts[r["image"]]
    write_manifest(rows)
    print(f"черновые рамки: {sum(counts.values())} на {len(counts)} снимках")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("batch", type=Path, help="папка партии с house_NNN/ и manifest.csv")
    parser.add_argument(
        "--max-per-house",
        type=int,
        default=0,
        help="размечать не больше N снимков дома (редкие стадии первыми), остальные — skip",
    )
    parser.add_argument("--prelabel", type=Path, help="веса модели для черновых рамок")
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--imgsz", type=int, default=1280)
    args = parser.parse_args()
    added = register(args.batch.resolve(), args.max_per_house)
    if args.prelabel and added:
        prelabel(added, args.prelabel, args.conf, args.imgsz)


if __name__ == "__main__":
    main()
