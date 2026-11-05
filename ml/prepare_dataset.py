"""Сборка датасета YOLO из ml/annotations/ с делением train/val по объектам.

    python ml/prepare_dataset.py                       # val из ml/annotations/val_objects.txt
    python ml/prepare_dataset.py --val-objects 7,10    # другой состав val
    python ml/prepare_dataset.py --exclude-objects 12  # без объекта 12 (супермаркет)
    python ml/prepare_dataset.py --include-prelabel    # взять и непроверенные рамки модели

Объект — папка dataset/<N> или house_<N> внешних фото: снимки одного дома не попадают в обе выборки
(docs/archive/BACKEND_PLAN.md, раздел 3.7). По умолчанию берутся только снимки со статусом reviewed.
Результат — data/yolo/{images,labels}/{train,val} и data/yolo/data.yaml; папка генерируется заново.
"""

from __future__ import annotations

import argparse
import os
import shutil
from collections import Counter
from pathlib import Path

import yaml
from common import (
    REPO,
    STATUS_PRELABEL,
    STATUS_REVIEWED,
    load_classes,
    load_val_objects,
    read_labels,
    read_manifest,
)

OUT_DIR = REPO / "data" / "yolo"


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)  # жёсткая ссылка: без копии снимков
    except OSError:
        shutil.copy2(src, dst)


def _object_key(obj: str) -> tuple[int, str]:
    """Папки dataset/ (числа) сначала по номеру, потом house_* по имени."""
    return (0, f"{int(obj):06d}") if obj.isdigit() else (1, obj)


def build(
    val_objects: set[str],
    exclude_objects: set[str] = frozenset(),
    out: Path = OUT_DIR,
    include_prelabel: bool = False,
) -> Path:
    """Собирает датасет и возвращает путь к data.yaml."""
    names = [c["name"] for c in load_classes()]
    statuses = {STATUS_REVIEWED, STATUS_PRELABEL} if include_prelabel else {STATUS_REVIEWED}
    rows = [
        r for r in read_manifest() if r["object"] not in exclude_objects and r["status"] in statuses
    ]
    missing = [r for r in rows if not (REPO / r["image"]).exists()]
    if missing:
        # Внешние фото не в git: у кого нет папки data/external/, тот учится без них.
        print(f"нет файлов снимков, пропущены: {len(missing)} (например, {missing[0]['image']})")
        rows = [r for r in rows if r not in missing]
    unknown = val_objects - {r["object"] for r in rows}
    if unknown:
        raise ValueError(
            f"объектов {sorted(unknown)} нет среди размеченных в manifest.csv (или они исключены)"
        )

    for split in ("train", "val"):
        for kind in ("images", "labels"):
            shutil.rmtree(out / kind / split, ignore_errors=True)
            (out / kind / split).mkdir(parents=True)

    stats = {"train": Counter(), "val": Counter()}
    images = Counter()
    for row in rows:
        split = "val" if row["object"] in val_objects else "train"
        src = REPO / row["image"]
        name = f"{row['object']}_{src.stem}"
        _link_or_copy(src, out / "images" / split / f"{name}{src.suffix}")
        boxes = read_labels(row["image"])
        # Материал стены — атрибут для классификатора, детектору нужен чистый YOLO из 5 колонок.
        (out / "labels" / split / f"{name}.txt").write_text(
            "".join(f"{b.cls} {b.cx:.6f} {b.cy:.6f} {b.w:.6f} {b.h:.6f}\n" for b in boxes),
            encoding="utf-8",
        )
        stats[split].update(names[b.cls] for b in boxes)
        images[split] += 1

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        yaml.safe_dump(
            {
                "path": str(out),
                "train": "images/train",
                "val": "images/val",
                "names": dict(enumerate(names)),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    objects = {r["object"] for r in rows}
    train_objects = sorted(objects - val_objects, key=_object_key)
    print(f"train: {images['train']} снимков, {len(train_objects)} объектов")
    print(
        f"val:   {images['val']} снимков, объекты {', '.join(sorted(val_objects, key=_object_key))}"
    )
    print(f"{'класс':<18}{'train':>7}{'val':>7}")
    for n in names:
        mark = "  <- нет примеров в train" if not stats["train"][n] else ""
        print(f"{n:<18}{stats['train'][n]:>7}{stats['val'][n]:>7}{mark}")
    print(data_yaml)
    return data_yaml


def parse_objects(value: str) -> set[str]:
    return {x.strip() for x in value.split(",") if x.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--val-objects",
        default=load_val_objects(),
        help="объекты для val через запятую; по умолчанию ml/annotations/val_objects.txt",
    )
    parser.add_argument("--exclude-objects", default="", help="объекты, которые не берём вовсе")
    parser.add_argument(
        "--include-prelabel",
        action="store_true",
        help="взять и снимки с непроверенными рамками модели",
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    build(
        parse_objects(args.val_objects),
        parse_objects(args.exclude_objects),
        args.out,
        args.include_prelabel,
    )


if __name__ == "__main__":
    main()
