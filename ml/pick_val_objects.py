"""Подбор контрольных объектов для детектора и классификатора материала.

    python ml/pick_val_objects.py              # показать состав и покрытие
    python ml/pick_val_objects.py --write      # записать ml/annotations/val_objects.txt

Объект целиком попадает либо в train, либо в val. Редкие классы и материалы обрабатываются первыми:
у каждого в val должно быть около VAL_SHARE рамок, и хотя бы один объект с ним остаётся в train.
CORE — съёмка в полном разрешении 5280x3956, где объекты мельче всего и видны разные стадии.
Список лежит в файле, чтобы состав val не менялся незаметно: метрики разных запусков сравнимы,
только пока он один и тот же.
"""

from __future__ import annotations

import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

from common import VAL_OBJECTS, load_classes, read_labels, read_manifest
from prepare_dataset import _object_key

CORE = {"6", "7", "10", "13"}
VAL_SHARE = 0.15
# Один дом под разными объектами: те же кадры из двух роликов (сравнение кадров по dHash).
SAME_HOUSE = [{"house_101", "house_114"}]


def collect(rows: list[dict]) -> dict[str, Counter]:
    """Рамки по объектам: число рамок класса и число стен каждого материала."""
    names = [c["name"] for c in load_classes()]
    per_object: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for box in read_labels(row["image"]):
            per_object[row["object"]][names[box.cls]] += 1
            if box.material:
                per_object[row["object"]][f"material:{box.material}"] += 1
    return per_object


def linked_groups(rows: list[dict]) -> list[frozenset[str]]:
    """Группы объектов, которые нельзя разводить по выборкам: общая страница-источник или один дом."""
    links = [{row["object"]} for row in rows]
    by_source: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["source"]:
            by_source[row["source"]].add(row["object"])
    groups: list[set[str]] = []
    for link in [*links, *by_source.values(), *SAME_HOUSE]:
        merged = set(link)
        for group in [g for g in groups if g & merged]:
            merged |= group
            groups.remove(group)
        groups.append(merged)
    return [frozenset(group) for group in groups]


def pick(per_object: dict[str, Counter], groups: list[frozenset[str]], seed: int) -> set[str]:
    """Набирает val целыми группами, чтобы связанные объекты не перекосили доли."""
    counts = {g: sum((per_object[o] for o in g), Counter()) for g in groups}
    totals = sum(counts.values(), Counter())
    order = sorted(groups, key=lambda g: sorted(g, key=_object_key))
    random.Random(seed).shuffle(order)
    val = {g for g in groups if g & CORE}
    for target in sorted(totals, key=totals.get):
        goal = max(1, round(totals[target] * VAL_SHARE))
        holders = [g for g in order if counts[g][target]]
        for group in sorted(holders, key=lambda g: sum(counts[g].values())):
            have = sum(counts[v][target] for v in val)
            if have >= goal or len(set(holders) - val) <= 1:
                break
            val.add(group)
    return set().union(*val)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--seed", type=int, default=2, help="2 — самый маленький val при том же покрытии"
    )
    parser.add_argument("--write", action="store_true", help="записать состав в val_objects.txt")
    args = parser.parse_args()

    rows = [r for r in read_manifest() if r["status"] == "reviewed"]
    per_object = collect(rows)
    val = pick(per_object, linked_groups(rows), args.seed)

    images = Counter(r["object"] for r in rows)
    sources = Counter(
        Path(r["image"]).parts[2 if r["image"].startswith("data/") else 0]
        for r in rows
        if r["object"] in val
    )
    val_images = sum(images[o] for o in val)
    print(f"val: {len(val)} объектов из {len(per_object)}, {val_images} снимков из {len(rows)}")
    print("источники val:", dict(sources))
    totals = sum(per_object.values(), Counter())
    held = sum((per_object[o] for o in val), Counter())
    print(f"{'цель':<28}{'всего':>7}{'val':>6}{'доля':>7}")
    for target in sorted(totals):
        print(
            f"{target:<28}{totals[target]:>7}{held[target]:>6}{held[target] / totals[target]:>7.0%}"
        )

    if args.write:
        VAL_OBJECTS.write_text("\n".join(sorted(val, key=_object_key)) + "\n", encoding="utf-8")
        print(VAL_OBJECTS)


if __name__ == "__main__":
    main()
